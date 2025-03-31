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


def rename_with_counter(old_name, new_name):
    """
    Make sure that new_name does not exists before renaming.
    If so, it appends a counter at the end until new_name + counter does not exists.
    """
    count = 0
    new_name_to_use = new_name
    while os.path.exists(new_name_to_use):
        count += 1
        new_name_to_use = new_name + str(count)
    os.rename(old_name, new_name_to_use)


def ensure_file_uniqueness(file_path):
    """
    Make sure the file_path is non existent, if so it appends a counter ( "/path/to/my_file (1)" ) at the end.
    """
    base, extension = os.path.splitext(file_path)
    count = 0
    uniq_path = file_path
    while os.path.exists(uniq_path):
        count += 1
        uniq_path = base + " (" + str(count) + ")" + extension
    return uniq_path


def truncate_folder_and_file_names(path, max_folder_length=57, max_files_length=200):
    for root, dirs, files in os.walk(path):
        for dir in dirs:
            if len(dir) > max_folder_length:
                old_name = os.path.join(root, dir)
                new_name = os.path.join(root, dir[:max_folder_length])
                print(f"Renaming folder {old_name} to {new_name}")  # noqa: T201
                rename_with_counter(old_name, new_name)
        for file in files:
            if len(file) > max_files_length:
                old_name = os.path.join(root, file)
                new_name = truncate_file_name(file, max_files_length)
                print(f"Renaming file {old_name} to {new_name}")  # noqa: T201
                rename_with_counter(old_name, new_name)


def truncate_file_name(filename, max_length=200):
    """
    Reduce the size of a filename in path keeping its extension.
    """
    if len(filename) <= max_length:
        return filename
    base, extension = os.path.splitext(filename)
    new_length = max_length - len(extension) - 1 if extension else max_length
    new_base = base[:new_length]
    return new_base + extension


def truncate_path_parts(path, max_folder_length=200, max_files_length=190):
    """
    Reduce each parts of path if needed.
    """
    head, filename = os.path.split(path)
    parts = head.split(os.sep)
    output = []
    for part in parts:
        output.append(part[:max_folder_length])
    output.append(truncate_file_name(filename, max_files_length))
    output_path = os.path.join(*output)
    if path.startswith(os.sep):
        output_path = os.sep + output_path
    return output_path
