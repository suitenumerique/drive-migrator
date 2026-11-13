"""Paths of the migrated files in the bucket Resana imports from."""

import os
from collections import defaultdict

# Longest folder name Resana accepts.
MAX_FOLDER_LENGTH = 57


def resana_dir_renames(dirs: list[str]) -> dict[str, str]:
    """
    The folders of a run (parents first) whose name is too long for Resana,
    mapped to their path in Resana. A truncated name colliding with a sibling
    gets a counter. Folders below a renamed one are only listed when renamed
    themselves: resana_path() carries them along.
    """
    children = defaultdict(list)
    for path in dirs:
        children[os.path.dirname(path)].append(path)

    renames = {}

    def visit(parent: str, resana_parent: str) -> None:
        names = [os.path.basename(path) for path in children[parent]]
        taken = {name for name in names if len(name) <= MAX_FOLDER_LENGTH}
        for path, name in zip(children[parent], names, strict=True):
            resana_name = name
            if len(name) > MAX_FOLDER_LENGTH:
                truncated = resana_name = name[:MAX_FOLDER_LENGTH]
                count = 0
                while resana_name in taken:
                    count += 1
                    resana_name = f"{truncated}{count}"
                taken.add(resana_name)
            resana_dir = os.path.join(resana_parent, resana_name)
            if resana_name != name:
                renames[path] = resana_dir
            visit(path, resana_dir)

    visit("", "")
    return renames


def resana_path(path: str, renames: dict[str, str]) -> str:
    """Path in Resana of a file of the run, given its folders' renames."""
    parent, suffix = os.path.dirname(path), [os.path.basename(path)]
    while parent:
        if parent in renames:
            return os.path.join(renames[parent], *reversed(suffix))
        suffix.append(os.path.basename(parent))
        parent = os.path.dirname(parent)
    return path
