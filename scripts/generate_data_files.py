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
from easybuild.framework.easyconfig.easyconfig import (
    process_easyconfig,
    get_toolchain_hierarchy,
)
from easybuild.tools.options import set_up_configuration
from easybuild.tools.include import include_easyblocks
from contextlib import contextmanager

VALID_EESSI_VERSIONS = ["2025.06", "2023.06"]

# Give order to my toolchains so I can easily figure out what "latest" means
EESSI_SUPPORTED_TOP_LEVEL_TOOLCHAINS = OrderedDict(
    {
        "2025.06": [
            {"name": "foss", "version": "2025b"},
            {"name": "foss", "version": "2025a"},
            {"name": "foss", "version": "2024a"},
        ],
        "2023.06": [
            {"name": "foss", "version": "2023b"},
            {"name": "foss", "version": "2023a"},
            {"name": "foss", "version": "2022b"},
        ],
    }
)


@contextmanager
def suppress_stdout():
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        yield
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout


def module_dict_from_module_string(module):
    module_name, module_version = module.split("/", 1)
    module_dict = {
        "module_name": module_name,
        "module_version": module_version,
        "full_module_name": module,
    }

    return module_dict


def load_and_list_modules(full_module_name):
    """
    Run `module load <name>` and `module list` inside a subshell.
    Returns the list of loaded modules visible inside that subshell.
    Does not modify Python's environment.
    """

    # Run as one shell script so the same session is used
    cmd = f"""
        module load {full_module_name} >/dev/null 2>&1 || exit 1
        module --terse list 2>&1
    """

    result = subprocess.run(["bash", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to load module '{full_module_name}':\n{result.stdout}")

    # Parse module list output
    modules = [module_dict_from_module_string(line) for line in result.stdout.splitlines() if "/" in line]

    # Filter out the modules we expect to be loaded
    eessi_extend_module_name = "EESSI-extend"
    eb_module_name = "EasyBuild"
    if full_module_name.startswith(f"{eessi_extend_module_name}/"):
        # Don't filter anything
        pass
    elif full_module_name.startswith(f"{eb_module_name}/"):
        # Filter EESSI-extend
        modules = [module for module in modules if module["module_name"] != eessi_extend_module_name]
    else:
        # Filter EESSI-extend and EasyBuild
        modules = [
            module
            for module in modules
            if module["module_name"] != eessi_extend_module_name and module["module_name"] != eb_module_name
        ]

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
    idx = len(parts) - 1 - parts[::-1].index("software")

    # Replace 'software' by 'reprod'
    parts[idx] = "reprod"

    # Path up to version directory (software/software/version)
    pre_timestamp = os.sep.join([""] + parts[: idx + 3])
    # Path after version directory (easybuild/reprod/easyblocks)
    post_version = parts[idx + 3 :]

    # Look for timestamp directories under pre_timestamp
    timestamp_dirs = [d for d in glob.glob(os.path.join(pre_timestamp, "*")) if os.path.isdir(d)]
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
    version_pattern = re.compile(r"software/EasyBuild/(\d+)\.(\d+)\.(\d+)/bin")

    # Get major version from loaded EasyBuild installation for exceptions
    easybuild_major_version = str(EASYBUILD_VERSION.version[0])

    # Find all .eb files recursively
    eb_files = glob.glob(os.path.join(base_path, "*/*/easybuild/*.eb"))

    for eb_file in eb_files:
        folder = os.path.dirname(eb_file)

        # Look for the -easybuild-devel file in the same folder
        devel_files = glob.glob(os.path.join(folder, "*-easybuild-devel"))
        if not devel_files:
            raise FileNotFoundError(f"No *-easybuild-devel file found in folder: {folder}")

        # Pick the latest devel file if multiple exist
        latest_devel = max(devel_files, key=os.path.getmtime)

        # Extract the EasyBuild version
        with open(latest_devel, "r") as f:
            content = f.read()
            match = version_pattern.search(content)

            # Handle exception folders
            if "EasyBuild" in folder or "EESSI-extend" in folder:
                major_version = match.group(1) if match else easybuild_major_version
                # Don't add EESSI-extend to EB4 or the same file will appear twice
                if "EESSI-extend" in folder and major_version == "4":
                    continue
            else:
                if not match:
                    raise ValueError(f"Cannot extract EasyBuild version from file: {latest_devel}")
                major_version = match.group(1)

            eb_files_by_version[f"{major_version}"].append(eb_file)

    return dict(eb_files_by_version)


def merge_dicts(d1, d2):
    merged = defaultdict(list)

    for d in (d1, d2):
        for key, value in d.items():
            merged[key].extend(value)

    return dict(merged)


if __name__ == "__main__":
    # The EESSI version is provided as an argument
    parser = argparse.ArgumentParser(description="EESSI version to scan.")
    parser.add_argument(
        "--eessi-version",
        "-e",
        required=True,
        choices=VALID_EESSI_VERSIONS,
        help=f"Allowed versions: {', '.join(VALID_EESSI_VERSIONS)}",
    )

    args = parser.parse_args()
    eessi_version = args.eessi_version

    print(f"Using EESSI version: {eessi_version}")

    # We use a single architecture path to gather information about the software versions
    eessi_reference_architecture = os.getenv("EESSI_ARCHDETECT_OPTIONS_OVERRIDE", False)
    if not eessi_reference_architecture:
        print("You must have selected a CPU architecture via EESSI_ARCHDETECT_OPTIONS_OVERRIDE")
        exit()
    base_path = f"/cvmfs/software.eessi.io/versions/{eessi_version}/software/linux/{eessi_reference_architecture}"
    cpu_easyconfig_files_dict = collect_eb_files(os.path.join(base_path, "software"))
    # We also gather all the acclerator installations for NVIDIA-enabled packages
    # We're not typically running this script on a node with a GPU so an override must have been set
    eessi_reference_nvidia_architecture = os.getenv("EESSI_ACCELERATOR_TARGET_OVERRIDE", False)
    if not eessi_reference_nvidia_architecture:
        print("You must have selected a GPU architecture via EESSI_ACCELERATOR_TARGET_OVERRIDE")
        exit()
    accel_base_path = os.path.join(base_path, eessi_reference_nvidia_architecture)
    accel_easyconfig_files_dict = collect_eb_files(os.path.join(accel_base_path, "software"))

    # Merge the easyconfig files
    easyconfig_files_dict = merge_dicts(cpu_easyconfig_files_dict, accel_easyconfig_files_dict)

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
        # versions are typically 2024a/2024b etc. for top level toolchains
        # so let's use that to make sorting easy
        toolchain_family = f"{top_level_toolchain['version']}_{top_level_toolchain['name']}"
        # Get the hierarchy and always add the system toolchain
        eessi_software["eessi_version"][eessi_version]["toolchain_hierarchy"][toolchain_family] = [
            {"name": "system", "version": "system"}
        ] + get_toolchain_hierarchy(top_level_toolchain)

    for eb_version_of_install, easyconfigs in sorted(easyconfig_files_dict.items()):
        print(f"Major version {eb_version_of_install}:")
        if eb_version_of_install == str(EASYBUILD_VERSION.version[0]):
            total_easyconfigs = len(easyconfigs)
            for i, easyconfig in enumerate(easyconfigs, start=1):
                percent = (i / total_easyconfigs) * 100
                print(f"{percent:.1f}% - {easyconfig}")

                # Don't try to parse an EasyBuild easyconfig that is not the same major release
                if "/software/EasyBuild/" in easyconfig and f"/EasyBuild/{eb_version_of_install}" not in easyconfig:
                    continue
                # print(process_easyconfig(path)[0]['ec'].asdict())

                eb_hooks_path = use_timestamped_reprod_if_exists(f"{os.path.dirname(easyconfig)}/reprod/easyblocks")
                
                # Store our easyblock-related state before including easyblocks (which modify all these)
                orig_sys_path = list(sys.path)
                import easybuild.easyblocks
                import easybuild.easyblocks.generic
                orig_easyblocks_path = list(easybuild.easyblocks.__path__)
                orig_generic_easyblocks_path = list(easybuild.easyblocks.generic.__path__)
                
                easyblocks_dir = include_easyblocks(tmpdir, [eb_hooks_path + "/*.py"])
                parsed_using_fallback = False
                try:
                    with suppress_stdout():
                        parsed_ec = process_easyconfig(easyconfig)[0]
                except Exception:
                    # There are cases where a an easyblock inherits from a class but also imports
                    # something from another easyblock which inherits from the same class, the import
                    # easyblock is not included in the reproducibility dir as it is not an inherited
                    # class. This can mean it may reference something that
                    # is not available in the "legacy" easyblock included by include_easyblock().
                    # Example is Tkinter, which inherits from EB_Python but also imports from
                    # pythonpackage (which also imports from EB_Python). pythonpackage is being
                    # picked up from the EasyBuild release being used for the parsing.

                    # Restore the original env and retry without include_easyblocks
                    for module in list(sys.modules):
                        if module.startswith("easybuild.easyblocks"):
                            del sys.modules[module]
                    sys.path[:] = orig_sys_path
                    easybuild.easyblocks.__path__[:] = orig_easyblocks_path
                    easybuild.easyblocks.generic.__path__[:] = orig_generic_easyblocks_path
                    try:
                        with suppress_stdout():
                            parsed_ec = process_easyconfig(easyconfig)[0]
                        print(f"Parsed {easyconfig} using fallback as using include_easyblocks() failed")
                        parsed_using_fallback = True
                    except Exception:
                        print(f"Fallback parsing of {easyconfig} without using include_easyblocks() failed!")
                        raise  # or should we break?
                finally:
                    easyblocks_used = [
                        os.path.basename(f)
                        for f in glob.glob(f"{easyblocks_dir}/**/*.py", recursive=True)
                        if os.path.basename(f) != "__init__.py"
                    ]
                    # ALWAYS restore
                    for module in list(sys.modules):
                        if module.startswith("easybuild.easyblocks"):
                            del sys.modules[module]
                    sys.path[:] = orig_sys_path
                    easybuild.easyblocks.__path__[:] = orig_easyblocks_path
                    easybuild.easyblocks.generic.__path__[:] = orig_generic_easyblocks_path

                    shutil.rmtree(easyblocks_dir, ignore_errors=True)

                # Store everything we now know about the installation as a dict
                # Use the path as the key since we know it is unique
                eessi_software["eessi_version"][eessi_version][easyconfig] = parsed_ec["ec"].asdict()
                if parsed_using_fallback:
                    eessi_software["eessi_version"][eessi_version][easyconfig]["parsed_with_eb_version_fallback"] = EASYBUILD_VERSION
                else:
                    eessi_software["eessi_version"][eessi_version][easyconfig]["parsed_with_eb_version_fallback"] = False
                eessi_software["eessi_version"][eessi_version][easyconfig]["mtime"] = os.path.getmtime(easyconfig)

                # Make sure we can load the module before adding it's information to the main dict
                try:
                    eessi_software["eessi_version"][eessi_version][easyconfig]["required_modules"] = (
                        load_and_list_modules(parsed_ec["full_mod_name"])
                    )
                except RuntimeError as e:
                    print(f"Ignoring {easyconfig} due to error processing module: {e}")
                    eessi_software["eessi_version"][eessi_version].pop(easyconfig)
                    continue

                # Add important data that is related to the module environment
                eessi_software["eessi_version"][eessi_version][easyconfig]["module"] = module_dict_from_module_string(
                    parsed_ec["full_mod_name"]
                )
                # Retain the easyblocks used so we can use a heuristic to figure out the type of extensions (R, Python, Perl)
                eessi_software["eessi_version"][eessi_version][easyconfig]["easyblocks"] = easyblocks_used

    # Store the result
    with open(
        f"eessi_software_{eessi_version}-eb{str(EASYBUILD_VERSION.version[0])}.yaml",
        "w",
    ) as f:
        yaml.dump(eessi_software, f)
