# Django impress

# ---- base image to inherit from ----
FROM python:3.14.6-alpine AS base

# Upgrade pip to its latest release to speed up dependencies installation
RUN python -m pip install --upgrade pip && \
  apk upgrade --no-cache

# ---- Back-end builder image ----
FROM base AS back-builder

# Configure uv
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be copied
# from the build image into the final image.
ENV UV_PYTHON_DOWNLOADS=0

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

WORKDIR /app

# Copy required python dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=src/backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=src/backend/pyproject.toml,target=pyproject.toml \
      uv sync --locked --no-install-project --no-dev
COPY src/backend /app
RUN --mount=type=cache,target=/root/.cache/uv \
      uv sync --locked --no-dev


# ---- mails ----
FROM node:22 AS mail-builder

COPY ./src/mail /mail/app

WORKDIR /mail/app

RUN yarn install --frozen-lockfile && \
    yarn build


# ---- static link collector ----
FROM back-builder AS link-collector
ARG MIGRATOR_STATIC_ROOT=/data/static

# Install libpangocairo & rdfind
RUN apk add --no-cache \
      pango \
      file \
      rdfind

# Copy the application from the builder
COPY --from=back-builder /app /app

WORKDIR /app

# collectstatic
RUN DJANGO_CONFIGURATION=Build DJANGO_JWT_PRIVATE_SIGNING_KEY=Dummy \
    uv run python manage.py collectstatic --noinput

# Replace duplicated file by a symlink to decrease the overall size of the
# final image
RUN rdfind \
  -makesymlinks true \
  -followsymlinks true \
  -makeresultsfile false \
  ${MIGRATOR_STATIC_ROOT}

# ---- Core application image ----
FROM base AS core

ENV PYTHONUNBUFFERED=1

# Install required system libs
RUN apk add --no-cache \
      cairo \
      gdk-pixbuf \
      gettext \
      libffi \
      libmagic \
      mailcap \
      pango \
      shared-mime-info

# Copy entrypoint
COPY ./docker/files/usr/local/bin/entrypoint /usr/local/bin/entrypoint

# Give the "root" group the same permissions as the "root" user on /etc/passwd
# to allow a user belonging to the root group to add new users; typically the
# docker user (see entrypoint).
RUN chmod g=u /etc/passwd

# Copy installed python dependencies
COPY --from=back-builder /app /app

WORKDIR /app

# We wrap commands run in this container by the following entrypoint that
# creates a user on-the-fly with the container user ID (see USER) and root group
# ID.
ENTRYPOINT [ "/usr/local/bin/entrypoint" ]

# ---- Development image ----
FROM core AS backend-development

ARG DOCKER_USER

# Configure uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/opt/cache

# Switch back to the root user to install development dependencies
USER root:root

# Install psql
RUN apk add --no-cache postgresql-client

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

# Install development dependencies and ensure the virtual
# environment belongs to the DOCKER_USER
RUN uv sync --all-extras --locked && \
    chown -R "${DOCKER_USER}" /opt/venv /opt/cache

# Restore the un-privileged user running the application
USER ${DOCKER_USER}

# Run django development server
CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]


# ---- Flower image ----
FROM backend-development AS celery-flower

# Switch back to the root user to install development dependencies
USER root:root

# Run django development server
CMD ["uv", "run", "celery", "-A", "main.celery_app", "flower"]

# ---- Production image ----
FROM core AS backend-production

ARG MIGRATOR_STATIC_ROOT=/data/static

# Gunicorn
RUN mkdir -p /usr/local/etc/gunicorn
COPY docker/files/usr/local/etc/gunicorn/main.py /usr/local/etc/gunicorn/main.py

# Un-privileged user running the application
ARG DOCKER_USER
USER ${DOCKER_USER}

# Copy statics
COPY --from=link-collector ${MIGRATOR_STATIC_ROOT} ${MIGRATOR_STATIC_ROOT}

# Copy impress mails
COPY --from=mail-builder /mail/backend/core/templates/mail /app/core/templates/mail

# The default command runs gunicorn WSGI server in impress's main module
CMD ["uv", "run", "gunicorn", "-c", "/usr/local/etc/gunicorn/main.py", "main.wsgi:application"]
