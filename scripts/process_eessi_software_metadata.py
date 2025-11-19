#!/usr/bin/env python3
import copy
import os
import sys
import yaml
import json
from easybuild.tools import LooseVersion

ARCHITECTURES = [
    "aarch64/generic",
    "aarch64/a64fx",
    "aarch64/neoverse_n1",
    "aarch64/neoverse_v1",
    "aarch64/nvidia/grace",
    "x86_64/generic",
    "x86_64/amd/zen2",
    "x86_64/amd/zen3",
    "x86_64/amd/zen4",
    "x86_64/intel/haswell",
    "x86_64/intel/skylake_avx512",
    "x86_64/intel/sapphirerapids",
    "x86_64/intel/icelake",
    "x86_64/intel/cascadelake",
]

TOOLCHAIN_FAMILIES = [
    'foss_2025a',
    'foss_2024a',
    'foss_2023b',
    'foss_2023a',
    'foss_2022b',
]

def get_software_information_by_filename(file_metadata, original_path=None, toolchain_families=None):
    # print(original_path)
    software = {}
    # Due to components and extensions we may return a few different entries, construct a base dict first to build from
    base_version_dict = {
        'homepage': file_metadata['homepage'],
        'license': [],
        'image': '',
        'categories': [],
        'toolchain': file_metadata['toolchain'],
        'toolchain_families_compatibility': [key for key in toolchain_families.keys() if file_metadata['toolchain'] in toolchain_families[key]],
        'modulename': file_metadata['short_mod_name'],
        'required_modules': file_metadata['required_modules'],
    }
    
    # Need to do a bit of checking to ensure that it is supported by the architectures
    # 1) Detect the architecture substring inside the path
    base_version_dict['cpu_arch'] = []
    detected_arch = None
    for arch in ARCHITECTURES:
        if f"/{arch}/" in original_path:
            detected_arch = arch
            break

    if detected_arch is None:
        raise RuntimeError("No known architecture matched in the input path.")
    
    # 2) Construct the modulefile path
    before_arch, _, _ = original_path.partition(detected_arch)
    modulefile = before_arch + detected_arch + "/modules/all/" + file_metadata['short_mod_name']

    # 3) Substitute each architecture and test module file existence
    modulefile = original_path.replace(detected_arch, arch)
    for arch in ARCHITECTURES:
        substituted_modulefile = modulefile.replace(detected_arch, arch)
        # os.path.exists is very expensive for CVMFS
        try:
            if os.path.basename(substituted_modulefile) in os.listdir(os.path.dirname(substituted_modulefile)):
                base_version_dict['cpu_arch'].append(arch)
        except (FileNotFoundError, PermissionError) as e:
            continue

    # TODO: Handle GPU arch later
    base_version_dict['gpu_arch'] = []

    # Now we can cycle throught the possibilities
    # - software application itself
    software[file_metadata['name']] = {'versions': []}
    version_dict = copy.deepcopy(base_version_dict)
    version_dict['description'] = file_metadata['description']  
    version_dict['version'] = file_metadata['version']
    version_dict['versionsuffix'] = file_metadata['versionsuffix']
    version_dict['type'] = "application"
    software[file_metadata['name']]['versions'].append(version_dict)
    # - Now extensions
    for ext in file_metadata['exts_list']:
        # extensions are tuples beginning with name and version
        if ext[0] not in software.keys():
            software[ext[0]] = {'versions': []}
        version_dict = copy.deepcopy(base_version_dict)
        version_dict['version'] = ext[1]
        version_dict['versionsuffix'] = ''
        if 'pythonpackage.py' in file_metadata['easyblocks']:
            version_dict['type'] = "Python package"
        elif 'rpackage.py' in file_metadata['easyblocks']:
            version_dict['type'] = "R package"
        elif 'perlmodule.py' in file_metadata['easyblocks']:
            version_dict['type'] = "Perl module"
        else:
            version_dict['type'] = "extension"
        version_dict['description'] = f"""{ext[0]} is a {version_dict["type"]} included in the module {version_dict["modulename"]}"""
        software[ext[0]]['versions'].append(version_dict)
    # - Finally components (may not exist in data)
    if 'components' in file_metadata.keys():
        for component in file_metadata['components']:
            # extensions are tuples beginning with name and version
            if component[0] not in software.keys():
                software[component[0]] = {'versions': []}
            version_dict = copy.deepcopy(base_version_dict)
            version_dict['version'] = component[1]
            version_dict['versionsuffix'] = ''
            version_dict['type'] = "Component"
            version_dict['description'] = f"""{component[0]} is a {version_dict["type"]} included in the module {version_dict["modulename"]}"""
            software[component[0]]['versions'].append(version_dict)
    return software


def get_all_software(eessi_files_by_eessi_version):
    # Let's brute force things, for every file gather all the information
    # and then once we have it decides who has best information
    all_software_information = {}
    for eessi_version in eessi_files_by_eessi_version.keys():
        files = [file for file in eessi_files_by_eessi_version[eessi_version].keys() if file.startswith('/cvmfs')]
        total = len(files)

        for filename in files:
            software_updates = get_software_information_by_filename(eessi_files_by_eessi_version[eessi_version][filename], original_path=filename, toolchain_families=eessi_files_by_eessi_version[eessi_version]['toolchain_hierarchy'])
            for software in software_updates.keys():
                if software not in all_software_information.keys():
                    all_software_information[software] = {'versions': []}
                all_software_information[software]['versions'].extend(software_updates[software]['versions'])

    # Now that we have all the information let's cherry pick common items from the latest version of packages
    for software in all_software_information.keys():
        # Just look for the latest toolchain family, that should have latest versions
        reference_version = None
        for toolchain_family in TOOLCHAIN_FAMILIES:
            for version in all_software_information[software]['versions']:
                if toolchain_family in version["toolchain_families_compatibility"]:
                    reference_version = version
        if reference_version is None:
            raise ValueError(f"No toolchain compatibility in {all_software_information[software]}")
        for top_level_info in ['description', 'homepage', 'license', 'image', 'categories']:
            all_software_information[software][top_level_info] = reference_version[top_level_info]
            # Now we can clean up all the duplication
            for version in all_software_information[software]['versions']:
                version.pop(top_level_info)

    return all_software_information

def main():
    if len(sys.argv) < 3:
        print("Usage: process_eessi_software_metadata.py input.yaml output.json")
        sys.exit(1)

    output_file = sys.argv[2]
    input_file = sys.argv[1]

    with open(input_file) as f:
        software_metadata = yaml.load(f, Loader=yaml.FullLoader) or {}
    
    # Construct a new data object to export for use by an API endpoint
    # - timestamp
    # - architectures_map (dict, maps architecture to architecture for specific EESSI version, e.g., no Zen5 in 2023.06 but Zen4 will be detected)
    # - gpu_architectures_map (dict, empty for now)
    # - category_details (dict, empty for now, imagine category name and description)
    # - software
    #   - software-name (list, filter on category)
    #     - description (from most recent version)
    #     - homepage (from most recent version)
    #     - license (list of dicts, empty for now, typically expect one entry)
    #       - name
    #       - identifier
    #       - url
    #       - description
    #     - image (url, empty for now)
    #     - categories (list, empty for now)
    #     - versions (list of dicts, filter on architecture, filter on toolchain_families_compatibility, filter on EESSI version)
    #       - type (package, R extension, Python extension, Perl extenson, component)
    #       - version
    #       - toolchain
    #       - toolchain_families_compatibility (list, constructed to be EESSI version specific so has implicit selection of EESSI version)
    #       - versionsuffix
    #       - cpu_arch (list)
    #       - gpu_arch (list, empty for now)
    #       - module_file
    #       - required_modules (list of modules)
    json_metadata = {"timestamp": software_metadata["timestamp"]}
    eessi_versions = software_metadata["eessi_version"].keys()
    json_metadata["architectures_map"] = {}
    for eessi_version in eessi_versions:
        json_metadata["architectures_map"][eessi_version] = {}
        for architecture in ARCHITECTURES:
            json_metadata["architectures_map"][eessi_version][architecture] = architecture
    json_metadata["gpu_architectures_map"] = {}
    json_metadata["category_details"] = {}
    json_metadata["software"] = get_all_software(software_metadata["eessi_version"])
    
    with open(output_file, "w") as out:
        json.dump(json_metadata, out)

    print(f"Successfully processed {input_file} to {output_file}")


if __name__ == "__main__":
    main()
