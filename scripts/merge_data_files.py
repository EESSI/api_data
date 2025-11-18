#!/usr/bin/env python3
import sys
import yaml

def strict_merge(a, b, path=""):
    """Recursively merge dictionary b into a, erroring on mismatched values."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        # If they are not both dicts, they must match exactly:
        if a != b:
            raise ValueError(f"Conflict at {path}: {a!r} != {b!r}")
        return a  # values identical, no change

    for key in b:
        sub_path = f"{path}.{key}" if path else key
        if key not in a:
            a[key] = b[key]
        else:
            a[key] = strict_merge(a[key], b[key], sub_path)
    return a


def main():
    if len(sys.argv) < 3:
        print("Usage: merge_yaml.py out.yaml file1.yaml file2.yaml ...")
        sys.exit(1)

    output_file = sys.argv[1]
    input_files = sys.argv[2:]

    merged = {}
    for filename in input_files:
        with open(filename) as f:
            data = yaml.load(f, Loader=yaml.FullLoader) or {}
            merged = strict_merge(merged, data)

    with open(output_file, "w") as out:
        yaml.dump(merged, out)

    print(f"Successfully merged into {output_file}")


if __name__ == "__main__":
    main()
