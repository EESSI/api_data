import os
import json
import hashlib

# Directory to scan
directory = "./"  # Change to your target directory

# Output file
output_file = "hashes.json"

# Function to compute SHA256 hash of a file
def compute_hash(file_path):
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

# Collect hashes
hashes = {}
for root, dirs, files in os.walk(directory):
    for file in files:
        if file.endswith((".json", ".yaml", ".yml")):
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, directory)
            hashes[relative_path] = compute_hash(file_path)

# Write hashes to JSON file
with open(output_file, "w") as f:
    json.dump(hashes, f, indent=4)

print(f"Hashes saved to {output_file}")
