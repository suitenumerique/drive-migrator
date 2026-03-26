"""Abstract destination backend interface."""

from abc import ABC, abstractmethod


class AbstractDestinationBackend(ABC):
    """
    Contract for any platform that can receive a migrated workspace.

    `name` must be a unique slug used as key in Workspace.destination_statuses.
    `label` is the human-readable label exposed to the UI.
    Implementations must be instantiable without arguments.

    Note: `name` and `label` are plain class attributes, not abstract properties.
    `__init_subclass__` enforces their presence at class definition time so that
    missing attributes raise TypeError immediately rather than at runtime.
    """

    name: str  # e.g. "archive", "resana", "drive"
    label: str  # Human-readable label for UI, e.g. "La Suite Drive"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Skip check for abstract intermediate classes
        if not getattr(cls, "__abstractmethods__", None):
            for attr in ("name", "label"):
                if not hasattr(cls, attr):
                    raise TypeError(f"{cls.__name__} must define class attribute '{attr}'")

    @abstractmethod
    def export(self, workspace, user, local_folder_path: str) -> None:
        """
        Export the locally assembled workspace to this destination.
        Must update workspace.destination_statuses[self.name] and call workspace.save().
        For async destinations (e.g. Resana job), set status to PENDING.
        For sync destinations (e.g. archive), set status to SUCCESS or FAILURE.
        """

    # --- Optional hooks (default: raise NotImplementedError) ---

    def get_download_url(self, workspace) -> str:
        """Return a download URL for the exported content (e.g. presigned S3 URL)."""
        raise NotImplementedError

    def get_error_details(self, workspace) -> list:
        """Return details of failed items for async destinations."""
        raise NotImplementedError

    def retry(self, workspace) -> None:
        """Retry a failed or partial export."""
        raise NotImplementedError

    def poll_completion(self, workspace) -> None:
        """
        Poll the destination for async job completion and update workspace status.
        Called on demand (e.g. via API endpoint).
        """
        raise NotImplementedError
