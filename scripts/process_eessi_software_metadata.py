#!/usr/bin/env python3
import sys
import yaml

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
    # - architectures (list)
    # - gpu_architectures (list, empty for now)
    # - categories (list, empty for now)
    # - toolchain_families_compatibility (list, constructed to be EESSI version specific so has implicit inclusion of EESSI version)
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
    #     - versions (list of dicts, filter on architecture, filter on toolchain_families_compatibility)
    #       - version
    #       - toolchain
    #       - toolchain_families_compatibility (list, constructed to be EESSI version specific so has implicit selection of EESSI version)
    #       - version_suffix
    #       - eessi_version
    #       - architectures (list)
    #       - gpu_architectures (list, empty for now)
    #       - module_file
    #       - module_environment (list of modules)
    # WIP

    with open(output_file, "w") as out:
        yaml.dump(merged, out)

    print(f"Successfully merged into {output_file}")


if __name__ == "__main__":
    main()
