"""Configuration mixins composed by the ``Base`` settings class.

The project's Django settings use `django-configurations
<https://django-configurations.readthedocs.io/>`_: environments are classes
(``Development``, ``Production``, ``Test``...) selected at runtime through the
``DJANGO_CONFIGURATION`` environment variable, and all of them live in
``main.settings`` so that ``DJANGO_SETTINGS_MODULE=main.settings`` keeps working.

To keep ``main/settings.py`` readable, the individual settings are grouped by
functional domain into the plain-class mixins exported here. ``Base`` composes
them, with ``configurations.Configuration`` listed **last** so the
django-configurations machinery (which resolves the ``values.*`` descriptors and
exposes the final settings) runs on the composed class.

Conventions:

* Each mixin is a plain class (NOT a ``Configuration`` subclass): the values are
  resolved once, on the final ``Base``.
* Every setting belongs to exactly one mixin; there is no overlap between them,
  so their composition order does not change behavior.
* Shared paths and the release helper live in :mod:`main.configurations.paths`.

See ``docs/backend-settings.md`` for the full layout and a recipe to add a new
setting.
"""

from .applications import ApplicationMixin
from .cache import CacheMixin
from .celery import CeleryMixin
from .cors import CorsMixin
from .database import DatabaseMixin
from .drive import DriveMixin
from .email import EmailMixin
from .general import GeneralMixin
from .i18n import I18nMixin
from .logging import LoggingMixin
from .migration import MigrationBackendsMixin
from .observability import ObservabilityMixin
from .oidc import OIDCMixin
from .osmose import OsmoseMixin
from .resana import ResanaMixin
from .rest_framework import DRFMixin
from .storage import StorageMixin

__all__ = [
    "ApplicationMixin",
    "CacheMixin",
    "CeleryMixin",
    "CorsMixin",
    "DRFMixin",
    "DatabaseMixin",
    "DriveMixin",
    "EmailMixin",
    "GeneralMixin",
    "I18nMixin",
    "LoggingMixin",
    "MigrationBackendsMixin",
    "OIDCMixin",
    "ObservabilityMixin",
    "OsmoseMixin",
    "ResanaMixin",
    "StorageMixin",
]
