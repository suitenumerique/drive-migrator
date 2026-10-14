"""Prometheus metrics, served on /metrics by the web and by each Celery worker."""

import logging
import threading
from secrets import compare_digest
from wsgiref.simple_server import WSGIRequestHandler, make_server

from django.conf import settings
from django.db import connection
from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_safe

from celery.signals import worker_ready
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    make_wsgi_app,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.multiprocess import MultiProcessCollector

from core.models import ExtraTaskInfo, Workspace

from main.celery_app import app

logger = logging.getLogger(__name__)

METRICS_PATH = "/metrics"

# Counted by the Celery workers, read on their own endpoint.
MIGRATION_DURATION = Histogram(
    "migrator_migration_duration_seconds",
    "Duration of the finished migrations, from queueing to the end of the task",
    ["status"],
    buckets=(10, 30, 60, 300, 900, 1800, 3600, 7200, 14400, 43200),
)
MIGRATED_FILES = Counter(
    "migrator_migrated_files_total", "Files migrated by the integrity-tracked runs"
)
SOURCE_FILES = Counter(
    "migrator_source_files_total", "Source files of the integrity-tracked runs"
)
DOWNLOAD_ERRORS = Counter(
    "migrator_download_errors_total", "Files that failed to download from the source"
)
DRIVE_UPLOAD_REJECTIONS = Counter(
    "migrator_drive_upload_rejections_total",
    "Files refused by Drive, by Drive error code",
    ["reason"],
)

INTEGRITY_LABELS = {True: "true", False: "false", None: "unknown"}


def is_authorized(authorization):
    """Tell whether an Authorization header carries the metrics key."""
    api_key = settings.PROMETHEUS_API_KEY
    # Bytes: compare_digest refuses non-ASCII strings.
    return bool(api_key) and compare_digest(
        (authorization or "").encode(), f"Bearer {api_key}".encode()
    )


class PrometheusAuthMiddleware:
    """Refuse the metrics endpoint to any caller without the bearer token."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.rstrip("/") == METRICS_PATH and not is_authorized(
            request.headers.get("Authorization")
        ):
            response = HttpResponse("Unauthorized", status=401)
            response["WWW-Authenticate"] = 'Bearer realm="metrics"'
            return response
        return self.get_response(request)


def record_migration_finished(extra_task, status):
    """Count a finished migration task."""
    MIGRATION_DURATION.labels(status).observe(
        (timezone.now() - extra_task.task_result.date_created).total_seconds()
    )
    summary = extra_task.integrity_report.get("summary", {})
    MIGRATED_FILES.inc(summary.get("migrated_files_count") or 0)
    SOURCE_FILES.inc(summary.get("source_files_count") or 0)


class WorkspaceCollector:
    """Read the state of the workspaces from the database at scrape time."""

    def collect(self):
        """Yield the workspace gauges."""
        workspaces = GaugeMetricFamily(
            "migrator_workspaces", "Workspaces by global status", labels=["status"]
        )
        for row in (
            Workspace.objects.values("status").annotate(count=Count("id")).order_by()
        ):
            workspaces.add_metric([row["status"]], row["count"])
        yield workspaces

        destinations = GaugeMetricFamily(
            "migrator_workspace_destinations",
            "Workspaces by destination and status of the export to it",
            labels=["destination", "status"],
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT entry.key, entry.value #>> '{}', COUNT(*) "
                "FROM core_workspace, jsonb_each(core_workspace.destination_statuses) "
                "AS entry GROUP BY 1, 2"
            )
            for destination, status, count in cursor.fetchall():
                destinations.add_metric([destination, status], count)
        yield destinations

        counts = Workspace.objects.aggregate(
            truncated=Count("id", filter=Q(is_truncated=True)),
            with_download_errors=Count("id", filter=~Q(download_errors=[])),
            with_upload_errors=Count("id", filter=~Q(upload_errors=[])),
        )
        for name, count in counts.items():
            yield GaugeMetricFamily(
                f"migrator_workspaces_{name}",
                f"Workspaces {name.replace('_', ' ')}, as of their last migration",
                value=count,
            )

        integrity = GaugeMetricFamily(
            "migrator_integrity_checks",
            "Migration runs by integrity check outcome",
            labels=["passed"],
        )
        for row in (
            ExtraTaskInfo.objects.values("integrity_check_passed")
            .annotate(count=Count("id"))
            .order_by()
        ):
            integrity.add_metric(
                [INTEGRITY_LABELS[row["integrity_check_passed"]]], row["count"]
            )
        yield integrity


class CeleryQueueCollector:
    """Ask the broker for the length of the Celery queue at scrape time."""

    def collect(self):
        """Yield the queue gauge, or nothing when the broker does not answer."""
        queue = app.conf.task_default_queue
        try:
            with app.connection_for_read() as broker:
                broker.ensure_connection(max_retries=1, timeout=2)
                channel = broker.default_channel
                # kombu keeps one Redis list per priority step
                length = sum(
                    channel.client.llen(
                        f"{queue}{channel.sep}{priority}" if priority else queue
                    )
                    for priority in channel.priority_steps
                )
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.debug("Could not read the Celery queue length", exc_info=True)
            return
        gauge = GaugeMetricFamily(
            "migrator_celery_queue_length",
            "Tasks waiting on the Celery queue, for the whole deployment",
            labels=["queue"],
        )
        gauge.add_metric([queue], length)
        yield gauge


@require_safe
def metrics_view(request):  # pylint: disable=unused-argument
    """Serve the metrics of the web processes and of the database."""
    registry = CollectorRegistry()
    MultiProcessCollector(registry)
    registry.register(WorkspaceCollector())
    registry.register(CeleryQueueCollector())
    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)


class _SilentHandler(WSGIRequestHandler):
    """Keep the scrapes out of the worker logs."""

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        """Log nothing."""


def protect(wsgi_app):
    """Wrap a WSGI app so that it requires the metrics bearer token."""

    def protected(environ, start_response):
        if not is_authorized(environ.get("HTTP_AUTHORIZATION")):
            start_response(
                "401 Unauthorized", [("WWW-Authenticate", 'Bearer realm="metrics"')]
            )
            return [b"Unauthorized"]
        return wsgi_app(environ, start_response)

    return protected


@worker_ready.connect
def start_worker_metrics_server(**_):
    """Serve the metrics of the Celery worker processes in a daemon thread."""
    if not settings.PROMETHEUS_METRICS_ENABLED:
        return
    registry = CollectorRegistry()
    MultiProcessCollector(registry)
    server = make_server(
        "",
        settings.PROMETHEUS_WORKER_METRICS_PORT,
        protect(make_wsgi_app(registry)),
        handler_class=_SilentHandler,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(
        "Serving the worker metrics on port %s", settings.PROMETHEUS_WORKER_METRICS_PORT
    )
