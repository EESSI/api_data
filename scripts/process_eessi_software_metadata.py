#!/usr/bin/env python3
import copy
import re
import os
import sys
import yaml
import json
import subprocess

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
    "foss_2025a",
    "foss_2024a",
    "foss_2023b",
    "foss_2023a",
    "foss_2022b",
]


def get_software_information_by_filename(file_metadata, original_path=None, toolchain_families=None):
    # print(original_path)
    # Due to components and extensions we may return a few different entries, construct a base dict first to build from
    base_version_dict = {
        "homepage": file_metadata["homepage"],
        "license": [],
        "image": "",
        "categories": [],
        "identifier": "",
        "toolchain": file_metadata["toolchain"],
        "toolchain_families_compatibility": [
            key for key in toolchain_families.keys() if file_metadata["toolchain"] in toolchain_families[key]
        ],
        "modulename": file_metadata["short_mod_name"],
        "required_modules": file_metadata["required_modules"],
    }

    # Need to do a bit of checking to ensure that it is supported by the architectures
    # 1) Detect the architecture substring inside the path
    base_version_dict["cpu_arch"] = []
    detected_arch = None
    for arch in ARCHITECTURES:
        if f"/{arch}/" in original_path:
            detected_arch = arch
            break

    if detected_arch is None:
        raise RuntimeError("No known architecture matched in the input path.")

    # 2) Construct the modulefile path
    before_arch, _, _ = original_path.partition(detected_arch)
    modulefile = before_arch + detected_arch + "/modules/all/" + file_metadata["short_mod_name"] + '.lua'
    spider_cache = before_arch + detected_arch + "/.lmod/cache/spiderT.lua"

    # 3) Substitute each architecture and test module file existence in spider cache
    for arch in ARCHITECTURES:
        substituted_modulefile = modulefile.replace(detected_arch, arch)
        substituted_spider_cache = spider_cache.replace(detected_arch, arch)
        # os.path.exists is very expensive for CVMFS so we just look for the file in the spider cache
        found = subprocess.run(["grep", "-q", substituted_modulefile, substituted_spider_cache]).returncode == 0
        if found:
            base_version_dict["cpu_arch"].append(arch)
        else:
            print(f"No module {substituted_modulefile}...not adding software for archtecture {arch}")
            continue

    # TODO: Handle GPU arch later, but it is going to need to be a dict as we will filter on cpu arch
    base_version_dict["gpu_arch"] = {}

    # Now we can cycle throught the possibilities
    # - software application itself
    software = {}
    software[file_metadata["name"]] = {"versions": []}
    version_dict = copy.deepcopy(base_version_dict)
    version_dict["description"] = file_metadata["description"]
    version_dict["version"] = file_metadata["version"]
    version_dict["versionsuffix"] = file_metadata["versionsuffix"]
    # No need for as we separate out the different types
    # version_dict['type'] = "application"
    software[file_metadata["name"]]["versions"].append(version_dict)
    # - Now extensions
    python_extensions = {}
    perl_extensions = {}
    r_extensions = {}
    octave_extensions = {}
    ruby_extensions = {}
    for ext in file_metadata["exts_list"]:
        version_dict = copy.deepcopy(base_version_dict)
        # (extensions are tuples beginning with name and version)
        version_dict["version"] = ext[1]
        version_dict["versionsuffix"] = ""
        # Add the parent software name so we can make a set for all versions
        version_dict["parent_software"] = {
            "name": file_metadata["name"],
            "version": file_metadata["version"],
            "versionsuffix": file_metadata["versionsuffix"],
        }
        # First we do a heuristic to figure out the type of extension
        if "pythonpackage.py" in file_metadata["easyblocks"]:
            version_dict["description"] = (
                f"""{ext[0]} is a Python package included in the software module for {version_dict['parent_software']['name']}"""
            )
            python_extensions[ext[0]] = {"versions": [], "parent_software": set()}
            python_extensions[ext[0]]["versions"].append(version_dict)
            python_extensions[ext[0]]["parent_software"].add(version_dict["parent_software"]["name"])
        elif "rpackage.py" in file_metadata["easyblocks"]:
            version_dict["description"] = (
                f"""{ext[0]} is an R package included in the software module for {version_dict['parent_software']['name']}"""
            )
            r_extensions[ext[0]] = {"versions": [], "parent_software": set()}
            r_extensions[ext[0]]["versions"].append(version_dict)
            r_extensions[ext[0]]["parent_software"].add(version_dict["parent_software"]["name"])
        elif "perlmodule.py" in file_metadata["easyblocks"]:
            version_dict["description"] = (
                f"""{ext[0]} is a Perl module package included in the software module for {version_dict['parent_software']['name']}"""
            )
            perl_extensions[ext[0]] = {"versions": [], "parent_software": set()}
            perl_extensions[ext[0]]["versions"].append(version_dict)
            perl_extensions[ext[0]]["parent_software"].add(version_dict["parent_software"]["name"])
        elif "octavepackage.py" in file_metadata["easyblocks"]:
            version_dict["description"] = (
                f"""{ext[0]} is an Octave package included in the software module for {version_dict['parent_software']['name']}"""
            )
            octave_extensions[ext[0]] = {"versions": [], "parent_software": set()}
            octave_extensions[ext[0]]["versions"].append(version_dict)
            octave_extensions[ext[0]]["parent_software"].add(version_dict["parent_software"]["name"])
        elif "rubygem.py" in file_metadata["easyblocks"]:
            version_dict["description"] = (
                f"""{ext[0]} is an Ruby gem included in the software module for {version_dict['parent_software']['name']}"""
            )
            ruby_extensions[ext[0]] = {"versions": [], "parent_software": set()}
            ruby_extensions[ext[0]]["versions"].append(version_dict)
            ruby_extensions[ext[0]]["parent_software"].add(version_dict["parent_software"]["name"])
        else:
            raise ValueError(
                f"Only known extension types are R, Python and Perl! Easyblocks used by {original_path} were {file_metadata['easyblocks']}"
            )
    # - Finally components (may not exist in data)
    components = {}
    if "components" in file_metadata.keys():
        for component in file_metadata["components"]:
            # extensions are tuples beginning with name and version
            if component[0] not in components.keys():
                components[component[0]] = {"versions": [], "parent_software": set()}
            version_dict = copy.deepcopy(base_version_dict)
            version_dict["version"] = component[1]
            version_dict["versionsuffix"] = ""
            version_dict["type"] = "Component"
            version_dict["parent_software"] = {
                "name": file_metadata["name"],
                "version": file_metadata["version"],
                "version": file_metadata["versionsuffix"],
            }
            version_dict["description"] = (
                f"""{component[0]} is a component included in the software module for {version_dict['parent_software']['name']}"""
            )
            components[component[0]]["versions"].append(version_dict)
            components[component[0]]["parent_software"].add(version_dict["parent_software"]["name"])
    # print(f"Software: {software}, Python: {python_extensions}, Perl: {perl_extensions}, R: {r_extensions}, Component: {components}")
    return software, {
        "python": python_extensions,
        "perl": perl_extensions,
        "r": r_extensions,
        "octave": octave_extensions,
        "ruby": ruby_extensions,
        "component": components,
    }


def get_all_software(eessi_files_by_eessi_version):
    # Let's brute force things, for every file gather all the information
    # and then once we have it decides who has best information
    all_software_information = {}
    all_extension_information = {}
    for eessi_version in eessi_files_by_eessi_version.keys():
        files = [file for file in eessi_files_by_eessi_version[eessi_version].keys() if file.startswith("/cvmfs")]
        total = len(files)

        for i, filename in enumerate(files, start=1):
            print(f"EESSI/{eessi_version}, {i} of {total}: {filename}")
            software_updates, extensions_updates = get_software_information_by_filename(
                eessi_files_by_eessi_version[eessi_version][filename],
                original_path=filename,
                toolchain_families=eessi_files_by_eessi_version[eessi_version]["toolchain_hierarchy"],
            )
            # initialise all the extension dicts
            for key in extensions_updates.keys():
                if key not in all_extension_information.keys():
                    all_extension_information[key] = {}
            for software in software_updates.keys():
                if software not in all_software_information.keys():
                    all_software_information[software] = {"versions": []}
                all_software_information[software]["versions"].extend(software_updates[software]["versions"])
            for key in all_extension_information.keys():
                for extension in extensions_updates[key].keys():
                    if extension not in all_extension_information[key].keys():
                        all_extension_information[key][extension] = {"versions": [], "parent_software": set()}
                    all_extension_information[key][extension]["versions"].extend(
                        extensions_updates[key][extension]["versions"]
                    )
                    all_extension_information[key][extension]["parent_software"].update(
                        extensions_updates[key][extension]["parent_software"]
                    )

    # Now that we have all the information let's cherry pick common items from the latest version of packages
    print(f"Total of {len(all_software_information.keys())} individual software packages")
    top_level_info_list = ["homepage", "license", "image", "categories", "identifier"]
    for software in all_software_information.keys():
        # Just look for the latest toolchain family, that should have latest versions
        reference_version = None
        for toolchain_family in TOOLCHAIN_FAMILIES:
            for version in all_software_information[software]["versions"]:
                if toolchain_family in version["toolchain_families_compatibility"]:
                    reference_version = version
        if reference_version is None:
            raise ValueError(f"No toolchain compatibility in {all_software_information[software]}")
        for top_level_info in top_level_info_list + ["description"]:
            all_software_information[software][top_level_info] = reference_version[top_level_info]
        #     # Now we can clean up all the duplication, but it save little space to do so and it may prove useful
        #     for version in all_software_information[software]['versions']:
        #         version.pop(top_level_info)

    # Do the same for extensions and components type
    module_text = {
        "perl": "Perl module packages",
        "python": "Python packages",
        "r": "R packages",
        "component": "software components",
        "octave": "octave package",
    }
    for key in all_extension_information.keys():
        print(f"Total of {len(all_extension_information[key].keys())} individual {key} packages")
        for software in all_extension_information[key].keys():
            # Just look for the latest toolchain family, that should have latest versions
            reference_version = None
            for toolchain_family in TOOLCHAIN_FAMILIES:
                for version in all_extension_information[key][software]["versions"]:
                    if toolchain_family in version["toolchain_families_compatibility"]:
                        reference_version = version
            if reference_version is None:
                raise ValueError(f"No toolchain compatibility in {all_extension_information[key][software]}")
            # description is a bit special for extensions (we replace the last word by the set, and pop the set since we no longer need it and will later dump to json)
            all_extension_information[key][software]["description"] = re.sub(
                r"\b[\w-]+\b(?=\s*$)",
                f"{all_extension_information[key][software].pop('parent_software')}",
                reference_version["description"],
            )
            for top_level_info in top_level_info_list:
                all_extension_information[key][software][top_level_info] = reference_version[top_level_info]
            #     # Now we can clean up all the duplication, but it save little space to do so and it may prove useful
            #     for version in all_software_information[software]['versions']:
            #         version.pop(top_level_info)

    return {"software": all_software_information, **all_extension_information}


def main():
    if len(sys.argv) < 3:
        print("Usage: process_eessi_software_metadata.py input.yaml output_stub")
        sys.exit(1)

    output_stub = sys.argv[2]
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
    base_json_metadata = {"timestamp": software_metadata["timestamp"]}
    eessi_versions = software_metadata["eessi_version"].keys()
    base_json_metadata["architectures_map"] = {}
    for eessi_version in eessi_versions:
        base_json_metadata["architectures_map"][eessi_version] = {}
        for architecture in ARCHITECTURES:
            base_json_metadata["architectures_map"][eessi_version][architecture] = architecture
    base_json_metadata["gpu_architectures_map"] = {}
    base_json_metadata["category_details"] = {}
    full_software_information = get_all_software(software_metadata["eessi_version"])
    for key in full_software_information.keys():
        json_metadata = copy.deepcopy(base_json_metadata)
        json_metadata["software"] = full_software_information[key]
        if key == "software":
            file_suffix = key
        else:
            # everything else is some kind of extension
            file_suffix = "ext-" + key
        with open(f"{output_stub}_{file_suffix}.json", "w") as out:
            json.dump(json_metadata, out)

    print(f"Successfully processed {input_file} to {output_stub}*.json")


if __name__ == "__main__":
    main()
