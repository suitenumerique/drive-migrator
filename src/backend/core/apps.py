"""Main Core application"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configuration class for the main core app."""

    name = "core"

    def ready(self):
        """Connect the signal receivers of the application."""
        # pylint: disable-next=import-outside-toplevel,unused-import
        from core import signals
