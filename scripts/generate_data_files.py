import argparse
import glob
import os
import re
import sys
import shutil
import tempfile
import subprocess
import yaml
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
from easybuild.tools.version import VERSION as EASYBUILD_VERSION
from easybuild.framework.easyconfig.easyconfig import process_easyconfig, get_toolchain_hierarchy
from easybuild.tools.options import set_up_configuration
from easybuild.tools.include import include_easyblocks
from contextlib import contextmanager

VALID_EESSI_VERSIONS = ["2025.06", "2023.06"]

EESSI_REFERENCE_ARCHITECTURE = "x86_64/intel/icelake"

# Give order to my toolchains so I can easily figure out what "latest" means
EESSI_SUPPORTED_TOP_LEVEL_TOOLCHAINS = OrderedDict({
    '2025.06': [
        {'name': 'foss', 'version': '2025a'},
        {'name': 'foss', 'version': '2024a'},
    ],
    '2023.06': [
        {'name': 'foss', 'version': '2023b'},
        {'name': 'foss', 'version': '2023a'},
        {'name': 'foss', 'version': '2022b'},
    ],
})

@contextmanager
def suppress_stdout():
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        yield
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout


def load_and_list_modules(module_name):
    """
    Run `module load <name>` and `module list` inside a subshell.
    Returns the list of loaded modules visible inside that subshell.
    Does not modify Python's environment.
    """

    # Run as one shell script so the same session is used
    cmd = f"""
        module load {module_name} || exit 1
        module --terse list 2>&1
    """

    result = subprocess.run(
        ["bash", "-c", cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to load module '{module_name}':\n{result.stdout}")

    # Parse module list output
    modules = [
        line
        for line in result.stdout.splitlines()
        if "/" in line
    ]
    
    # Filter out the modules we expect to be loaded
    eessi_extend_module_stub = 'EESSI-extend/'
    eb_module_stub = 'EasyBuild/'
    if module_name.startswith(eessi_extend_module_stub):
        # Don't filter anything
        pass
    elif module_name.startswith(eb_module_stub):
        # Filter EESSI-extend
        modules = [module for module in modules if not module.startswith(eessi_extend_module_stub)]
    else:
        # Filter EESSI-extend and EasyBuild
        modules = [module for module in modules if not module.startswith(eessi_extend_module_stub) and not module.startswith(eb_module_stub)]

    return modules


def use_timestamped_reprod_if_exists(original_path):
    """
    Replace the last 'software' with 'reprod' and insert the latest timestamp directory
    after the version directory if it exists.
    """
    # Default to returning the original path
    returned_path = original_path
    
    # Split path
    parts = original_path.strip(os.sep).split(os.sep)

    # Find the last occurrence of 'software'
    idx = len(parts) - 1 - parts[::-1].index('software')

    # Replace 'software' by 'reprod'
    parts[idx] = 'reprod'

    # Path up to version directory (software/software/version)
    pre_timestamp = os.sep.join([''] + parts[:idx+3])
    # Path after version directory (easybuild/reprod/easyblocks)
    post_version = parts[idx+3:]

    # Look for timestamp directories under pre_timestamp
    timestamp_dirs = [d for d in glob.glob(os.path.join(pre_timestamp, '*')) if os.path.isdir(d)]
    if timestamp_dirs:
        latest_timestamp = max(timestamp_dirs)  # lexicographic order
        # Reconstruct path: reprod/.../version/<latest_timestamp>/easybuild/reprod/easyblocks
        final_path = os.path.join(pre_timestamp, latest_timestamp, *post_version)
        if os.path.exists(final_path):
            returned_path = final_path

    return returned_path

def collect_eb_files(base_path):
    """
    Scan for .eb files and their corresponding *-easybuild-devel files,
    extract the major EasyBuild version from devel files, and group .eb files by major version.
    For folders containing 'EasyBuild' or 'EESSI-extend', assume the loaded EasyBuild version if extraction fails.
    
    Parameters:
        base_path (str): Root folder to scan for .eb files.

    Returns:
        dict: {major_version: [list of .eb file paths]}
    """
    eb_files_by_version = defaultdict(list)
    version_pattern = re.compile(r'software/EasyBuild/(\d+)\.(\d+)\.(\d+)/bin')

    # Get major version from loaded EasyBuild installation for exceptions
    easybuild_major_version = str(EASYBUILD_VERSION.version[0])

    # Find all .eb files recursively
    eb_files = glob.glob(os.path.join(base_path, '*/*/easybuild/*.eb'))

    for eb_file in eb_files:
        folder = os.path.dirname(eb_file)

        # Look for the -easybuild-devel file in the same folder
        devel_files = glob.glob(os.path.join(folder, '*-easybuild-devel'))
        if not devel_files:
            raise FileNotFoundError(f"No *-easybuild-devel file found in folder: {folder}")

        # Pick the latest devel file if multiple exist
        latest_devel = max(devel_files, key=os.path.getmtime)

        # Extract the EasyBuild version
        with open(latest_devel, 'r') as f:
            content = f.read()
            match = version_pattern.search(content)

            # Handle exception folders
            if 'EasyBuild' in folder or 'EESSI-extend' in folder:
                major_version = match.group(1) if match else easybuild_major_version
                # Don't add EESSI-extend to EB4 or the same file will appear twice
                if 'EESSI-extend' in folder and major_version == '4':
                    continue
            else:
                if not match:
                    raise ValueError(f"Cannot extract EasyBuild version from file: {latest_devel}")
                major_version = match.group(1)

            eb_files_by_version[f"{major_version}"].append(eb_file)

    return dict(eb_files_by_version)


if __name__ == "__main__":
    # The EESSI version is provided as an argument
    parser = argparse.ArgumentParser(description="EESSI version to scan.")
    parser.add_argument(
        "--eessi-version",
        "-e",
        required=True,
        choices=VALID_EESSI_VERSIONS,
        help=f"Allowed versions: {', '.join(VALID_EESSI_VERSIONS)}"
    )

    args = parser.parse_args()
    eessi_version = args.eessi_version

    print(f"Using EESSI version: {eessi_version}")

    # We use a single architecture path to gather information about the software versions
    base_path = f'/cvmfs/software.eessi.io/versions/{eessi_version}/software/linux/{EESSI_REFERENCE_ARCHITECTURE}/software/'
    result = collect_eb_files(base_path)

    set_up_configuration(args="")
    tmpdir = tempfile.mkdtemp()

    # Store all our data in a dict
    eessi_software = {"eessi_version": {}}
    eessi_software["eessi_version"][eessi_version] = {}
    # Add a timestamp
    eessi_software["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Store the toolchain hierarchies supported by the EESSI version
    eessi_software["eessi_version"][eessi_version]["toolchain_hierarchy"] = {}
    for top_level_toolchain in EESSI_SUPPORTED_TOP_LEVEL_TOOLCHAINS[eessi_version]:
        toolchain_family = f"{top_level_toolchain['name']}_{top_level_toolchain['version']}"
        # Get the hierarchy and always add the system toolchain
        eessi_software["eessi_version"][eessi_version]["toolchain_hierarchy"][toolchain_family] = [{'name': 'system', 'version': 'system'}] + get_toolchain_hierarchy(top_level_toolchain)
    
    for eb_version_of_install, files in sorted(result.items()):
        print(f"Major version {eb_version_of_install}:")
        if eb_version_of_install == str(EASYBUILD_VERSION.version[0]):
            total_files = len(files)
            for i, file in enumerate(files, start=1):
                percent = (i / total_files) * 100
                print(f"{percent:.1f}% - {file}")
    
                # Don't try to parse an EasyBuild easyconfig that is not the same major release
                if '/software/EasyBuild/' in file and f'/EasyBuild/{eb_version_of_install}' not in file:
                    continue
                # print(process_easyconfig(path)[0]['ec'].asdict())
                
                eb_hooks_path = use_timestamped_reprod_if_exists(f"{os.path.dirname(file)}/reprod/easyblocks")
                easyblocks_dir = include_easyblocks(tmpdir, [eb_hooks_path+"/*.py"])
                with suppress_stdout():
                    parsed_ec=process_easyconfig(file)[0]
                # included easyblocks are the first entry in sys.path, so just pop them but keep a list of what was used
                sys.path.pop(0)
                easyblocks_used = [os.path.basename(f) for f in glob.glob(f"{easyblocks_dir}/**/*.py", recursive=True) if os.path.basename(f) != '__init__.py']
                shutil.rmtree(easyblocks_dir)
                
                # Use the path as the key since we know it is unique
                eessi_software["eessi_version"][eessi_version][file] = parsed_ec['ec'].asdict()
                eessi_software["eessi_version"][eessi_version][file]['mtime'] = os.path.getmtime(file)
                
                # Make sure we can load the module before adding it's information to the main dict
                try:
                    eessi_software["eessi_version"][eessi_version][file]['required_modules'] = load_and_list_modules(parsed_ec['full_mod_name'])
                except RuntimeError as e:
                    print(f"Ignoring {file} due to error processing module: {e}")
                    eessi_software["eessi_version"][eessi_version].pop(file)
                    continue

                # Store everything we now know about the installation as a dict
                # Add important data that is related to the module environment
                eessi_software["eessi_version"][eessi_version][file]['full_mod_name'] = parsed_ec['full_mod_name']
                eessi_software["eessi_version"][eessi_version][file]['short_mod_name'] = parsed_ec['short_mod_name']
                eessi_software["eessi_version"][eessi_version][file]['required_modules'] = load_and_list_modules(parsed_ec['full_mod_name'])
                # Retain the easyblocks used so we can use a heuristic to figure out the type of extensions (R, Python, Perl)
                eessi_software["eessi_version"][eessi_version][file]['easyblocks'] = easyblocks_used
    
    # Store the result
    with open(f"eessi_software_{eessi_version}-eb{str(EASYBUILD_VERSION.version[0])}.yaml", "w") as f:
        yaml.dump(eessi_software, f)
