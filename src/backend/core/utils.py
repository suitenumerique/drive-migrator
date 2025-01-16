import os

from core.models import FeatureFlag


def get_dir_size(path="."):
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    return total


def sizeof_fmt(num, suffix="B"):
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def is_feature(name: str) -> bool:
    flag = FeatureFlag.objects.filter(name=name).first()
    if flag is None:
        return True
    return flag.is_active


def truncate_folder_and_file_names(path, max_folder_length=57, max_files_length=300):
    for root, dirs, files in os.walk(path):
        for dir in dirs:
            if len(dir) > max_folder_length:
                old_name = os.path.join(root, dir)
                new_name = os.path.join(root, dir[:max_folder_length])
                print(f"Renaming folder {old_name} to {new_name}")  # noqa: T201
                os.rename(old_name, new_name)
        for file in files:
            if len(file) > max_files_length:
                base, extension = os.path.splitext(file)
                new_length = (
                    max_files_length - len(extension) - 1
                    if extension
                    else max_files_length
                )
                new_base = base[:new_length]
                old_name = os.path.join(root, file)
                new_name = os.path.join(root, new_base + extension)
                print(f"Renaming file {old_name} to {new_name}")  # noqa: T201
                os.rename(old_name, new_name)
