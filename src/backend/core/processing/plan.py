"""In-memory migration plan: where each source file lands in the migrated tree."""

import os
from dataclasses import dataclass, field

from celery.utils.log import get_task_logger

from core.backends.source import SourceFile, SourceFolder
from core.utils import (
    ensure_file_uniqueness,
    sanitize_path_component,
    truncate_path_parts,
)

logger = get_task_logger(__name__)


@dataclass
class PlannedFile:
    """A source file and its path relative to the workspace root."""

    source: SourceFile
    path: str


@dataclass
class RefusedFile:
    """A source file left out of the migration, and why."""

    source: SourceFile
    path: str
    error: str


@dataclass
class MigrationPlan:
    """Folders (parents first) and files (in download order) of a workspace,
    and the files refused because their name escapes their folder."""

    dirs: list[str] = field(default_factory=list)
    files: list[PlannedFile] = field(default_factory=list)
    refused: list[RefusedFile] = field(default_factory=list)


def build_plan(root: SourceFolder) -> MigrationPlan:
    """
    Compute the sanitized, truncated and unique path of every folder and file
    of the tree. The root folder is virtual: its children are the top level.
    """
    plan = MigrationPlan()
    _plan_folder(root, "", plan, taken=set())
    return plan


def _plan_folder(
    folder: SourceFolder, current_dir: str, plan: MigrationPlan, taken: set
) -> None:
    for child in folder.children:
        path = truncate_path_parts(
            os.path.join(current_dir, sanitize_path_component(child.name))
        )
        error = _escape_error(child.name, current_dir)
        if error:
            logger.error("Refusing folder %s: %s", child.name, error)
            _refuse_folder(child, path, plan, error)
            continue
        # Same-named siblings share one folder.
        if path not in taken:
            taken.add(path)
            plan.dirs.append(path)
        _plan_folder(child, path, plan, taken)

    for file in folder.files:
        error = _escape_error(file.name_with_extension, current_dir)
        if error:
            logger.error("Refusing file %s: %s", file.name_with_extension, error)
            plan.refused.append(
                RefusedFile(
                    source=file,
                    path=_sanitized_path(current_dir, file.name_with_extension),
                    error=error,
                )
            )
            continue
        path = _plan_file_path(file, current_dir, taken)
        taken.add(path)
        plan.files.append(PlannedFile(source=file, path=path))


def _escape_error(name: str, current_dir: str) -> str | None:
    """Why name, joined to current_dir, would not be an entry of it, if so."""
    # Under a virtual workspace root, so a ".." at the top level escapes it
    # instead of collapsing on "/".
    directory = os.path.normpath(os.path.join(os.sep, "workspace", current_dir))
    resolved = os.path.normpath(os.path.join(directory, name))
    if resolved == directory or not resolved.startswith(directory + os.sep):
        return f"{os.path.join(current_dir, name)} is not inside {current_dir or '.'}"
    return None


def _refuse_folder(
    folder: SourceFolder, path: str, plan: MigrationPlan, error: str
) -> None:
    """Refuse every file of a refused folder, and of its sub-folders."""
    for file in folder.files:
        plan.refused.append(
            RefusedFile(
                source=file,
                path=_sanitized_path(path, file.name_with_extension),
                error=error,
            )
        )
    for child in folder.children:
        _refuse_folder(child, _sanitized_path(path, child.name), plan, error)


def _sanitized_path(current_dir: str, name: str) -> str:
    return os.path.join(current_dir, sanitize_path_component(name))


def _plan_file_path(file: SourceFile, current_dir: str, taken: set) -> str:
    path = _sanitized_path(current_dir, file.name_with_extension)
    truncated = truncate_path_parts(path)
    if truncated != path:
        logger.info("Truncated filename: %s into %s", path, truncated)

    unique = ensure_file_uniqueness(truncated, exists=taken.__contains__)
    if unique != truncated:
        logger.info("Uniquenessify filename: %s into %s", truncated, unique)
    return unique
