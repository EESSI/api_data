import json
import sys
from pathlib import Path

def explain_json_schema(obj, indent=0):
    spacing = "  " * indent
    explanation = ""

    if isinstance(obj, dict):
        explanation += f"{spacing}Object {{\n"
        for key, value in obj.items():
            explanation += f"{spacing}  '{key}': {explain_json_schema(value, indent + 1)}\n"
        explanation += f"{spacing}}}"
    elif isinstance(obj, list):
        if len(obj) == 0:
            explanation += "Array[]"
        else:
            types_in_array = {type(item).__name__ for item in obj}
            type_str = ", ".join(types_in_array)
            explanation += f"Array[{type_str}] (example: {explain_json_schema(obj[0], indent + 1)})"
    else:
        explanation += type(obj).__name__
    return explanation

def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_schema_md.py <path_to_json_file>")
        sys.exit(1)

    json_path = Path(sys.argv[1])
    if not json_path.is_file():
        print(f"Error: File not found: {json_path}")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    md_content = f"""

- [{json_path.name}]({json_path.name})

    ??? info "JSON Schema: {json_path.name}"
    
        ```text
{explain_json_schema(data, indent=8)}
        ```
"""

    print(md_content)

if __name__ == "__main__":
    main()
