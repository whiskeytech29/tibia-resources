import os
import uuid

def rename_files_to_lowercase(folder_path):
    for filename in os.listdir(folder_path):
        old_path = os.path.join(folder_path, filename)

        if not os.path.isfile(old_path):
            continue

        lower_name = filename.lower()

        # Already lowercase → skip
        if filename == lower_name:
            continue

        temp_name = f".__tmp__{uuid.uuid4().hex}"
        temp_path = os.path.join(folder_path, temp_name)
        new_path = os.path.join(folder_path, lower_name)

        # Step 1: rename to temp
        os.rename(old_path, temp_path)
        # Step 2: rename to final lowercase name
        os.rename(temp_path, new_path)

        print(f"Renamed: {filename} -> {lower_name}")


if __name__ == "__main__":
    folder = r"D:\CodeProject2025\tibia-resources\sprites\Nemesis"  # change this
    rename_files_to_lowercase(folder)