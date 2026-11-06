# Backend settings layout

The backend uses [django-configurations](https://django-configurations.readthedocs.io/):
each environment is a class, and the one to use is picked at runtime through the
`DJANGO_CONFIGURATION` environment variable (while `DJANGO_SETTINGS_MODULE` stays
`main.settings`). See `docker-compose.yml`, `manage.py`, `main/wsgi.py` and
`main/celery_app.py` for how it is wired.

To keep things readable, the individual settings are grouped by functional
domain into mixins under `main/configurations/`. The `Base` class in
`main/settings.py` composes those mixins, and the environment classes inherit
from `Base`.

## File layout

```
main/
  settings.py              # Base (composes the mixins) + environment classes
  configurations/
    __init__.py            # re-exports every mixin
    paths.py               # BASE_DIR, DATA_DIR, get_release()
    <domain>.py            # one mixin per domain
```

- `Base(GeneralMixin, ..., Configuration)` lists the mixins first and
  `configurations.Configuration` **last**, so django-configurations resolves the
  `values.*` descriptors on the composed class.
- Each mixin is a plain class (not a `Configuration` subclass): a given setting
  must live in exactly one mixin, so composition order never changes behavior.
- `Base` itself only keeps the `ENVIRONMENT` / `RELEASE` properties and the
  `post_setup` hook (Sentry/PostHog initialization).

## Which mixin owns what

| Mixin | File | Domain |
| --- | --- | --- |
| `GeneralMixin` | `general.py` | Debug flags, secret key, allowed hosts, URLs, app user, misc |
| `DatabaseMixin` | `database.py` | PostgreSQL connection (`DB_*`) |
| `CacheMixin` | `cache.py` | Redis cache and sessions |
| `StorageMixin` | `storage.py` | Static/media files and S3 (`AWS_S3_*`) |
| `ApplicationMixin` | `applications.py` | `INSTALLED_APPS`, `MIDDLEWARE`, templates, auth backends |
| `DRFMixin` | `rest_framework.py` | Django REST Framework and the OpenAPI schema |
| `I18nMixin` | `i18n.py` | Languages, locale paths, timezone |
| `EmailMixin` | `email.py` | SMTP (`EMAIL_*`) |
| `CorsMixin` | `cors.py` | CORS (`CORS_*`) |
| `LoggingMixin` | `logging.py` | `LOGGING` |
| `ObservabilityMixin` | `observability.py` | Sentry, PostHog, thumbnails |
| `CeleryMixin` | `celery.py` | Celery (`CELERY_*`) |
| `OIDCMixin` | `oidc.py` | OpenID Connect and login/logout redirects |
| `MigrationBackendsMixin` | `migration.py` | Source/destination backends and migration tuning |
| `ResanaMixin` | `resana.py` | Resana / Interstis source (`RESANA_*`) |
| `DriveMixin` | `drive.py` | La Suite Drive destination (`DRIVE_*`, `HELP_*`) |
| `OsmoseMixin` | `osmose.py` | Osmose source (`OSMOSE_*`) |

## Adding a new setting

1. Pick the mixin that owns the domain (or add a new mixin file and export it
   from `configurations/__init__.py`, then add it to the `Base` bases in
   `settings.py`).
2. Add the attribute. To make it overridable through the environment, use a
   `values.*` descriptor with `environ_name="MY_SETTING"` and
   `environ_prefix=None` (the project does not use the default `DJANGO_` prefix),
   for example:

   ```python
   MY_SETTING = values.IntegerValue(
       30, environ_name="MY_SETTING", environ_prefix=None
   )
   ```

3. Read it in the application through `from django.conf import settings` as usual.
4. Add a line to `CHANGELOG.md` under `## [Unreleased]`.

Keep settings that reference each other in the same mixin (e.g.
`DRIVE_API_BASE_URL` defaults to `DRIVE_BASE_URL`), since that reference happens
inside a single class body.
