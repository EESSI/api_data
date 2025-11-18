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
    # software-name
    #   - description (from most recent version)
    #   - homepage (from most recent version)
    #   - license (list, empty for now)
    #   - image (url, empty for now)
    #   - categories (list, empty for now)
    #   - versions (list of dicts)
    #     - version_suffix
    #     - eessi_version
    #     - architectures (list)
    #     - module_file
    #     - module_environment (list of modules)
    # WIP

    with open(output_file, "w") as out:
        yaml.dump(merged, out)

    print(f"Successfully merged into {output_file}")


if __name__ == "__main__":
    main()
