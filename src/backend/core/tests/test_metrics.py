"""Tests for the Prometheus metrics."""

import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory
from django.utils import timezone

import pytest
from django_celery_results.models import TaskResult
from prometheus_client import REGISTRY, values

from core import factories
from core.metrics import (
    CeleryQueueCollector,
    PrometheusAuthMiddleware,
    metrics_view,
    protect,
    record_migration_finished,
    start_worker_metrics_server,
)
from core.models import ExtraTaskInfo, Workspace

from main.celery_app import app
from main.settings import Base


@pytest.mark.parametrize(
    "api_key,authorization",
    [
        ("secret", None),
        ("secret", "Bearer wrong"),
        ("secret", "Bearer sécret"),
        ("secret", "secret"),
        (None, "Bearer None"),
        ("", "Bearer "),
    ],
)
def test_auth_middleware_refuses_the_metrics_without_the_key(
    settings, api_key, authorization
):
    settings.PROMETHEUS_API_KEY = api_key
    get_response = MagicMock()
    headers = {"HTTP_AUTHORIZATION": authorization} if authorization else {}

    response = PrometheusAuthMiddleware(get_response)(
        RequestFactory().get("/metrics", **headers)
    )

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == 'Bearer realm="metrics"'
    get_response.assert_not_called()


@pytest.mark.parametrize("path", ["/metrics", "/metrics/"])
def test_auth_middleware_lets_the_key_through(settings, path):
    settings.PROMETHEUS_API_KEY = "secret"
    get_response = MagicMock()

    response = PrometheusAuthMiddleware(get_response)(
        RequestFactory().get(path, HTTP_AUTHORIZATION="Bearer secret")
    )

    assert response == get_response.return_value


def test_auth_middleware_ignores_the_other_paths(settings):
    settings.PROMETHEUS_API_KEY = "secret"
    get_response = MagicMock()

    response = PrometheusAuthMiddleware(get_response)(
        RequestFactory().get("/api/v1.0/workspaces/")
    )

    assert response == get_response.return_value


@pytest.mark.django_db
def test_metrics_view_reports_the_workspaces(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    truncated = factories.WorkspaceFactory(
        status=Workspace.Status.SUCCESS,
        destination_statuses={"drive": Workspace.Status.SUCCESS},
        is_truncated=True,
        download_errors=[{"id": "1"}],
    )
    factories.WorkspaceFactory(
        status=Workspace.Status.FAILURE,
        destination_statuses={
            "drive": Workspace.Status.FAILURE,
            "archive": Workspace.Status.SUCCESS,
        },
        upload_errors=[{"path": "a"}],
    )
    factories.WorkspaceFactory()
    for task_id, passed in [("t1", False), ("t2", True), ("t3", None)]:
        ExtraTaskInfo.objects.create(
            task_result=TaskResult.objects.create(task_id=task_id),
            workspace=truncated,
            integrity_check_passed=passed,
        )

    with patch.object(CeleryQueueCollector, "collect", return_value=iter([])):
        response = metrics_view(RequestFactory().get("/metrics"))

    content = response.content.decode()
    for line in [
        'migrator_workspaces{status="SUCCESS"} 1.0',
        'migrator_workspaces{status="FAILURE"} 1.0',
        'migrator_workspaces{status="NONE"} 1.0',
        'migrator_workspace_destinations{destination="drive",status="SUCCESS"} 1.0',
        'migrator_workspace_destinations{destination="drive",status="FAILURE"} 1.0',
        'migrator_workspace_destinations{destination="archive",status="SUCCESS"} 1.0',
        "migrator_workspaces_truncated 1.0",
        "migrator_workspaces_with_download_errors 1.0",
        "migrator_workspaces_with_upload_errors 1.0",
        'migrator_integrity_checks{passed="false"} 1.0',
        'migrator_integrity_checks{passed="true"} 1.0',
        'migrator_integrity_checks{passed="unknown"} 1.0',
    ]:
        assert line in content


def test_metrics_view_refuses_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    response = metrics_view(RequestFactory().post("/metrics"))

    assert response.status_code == 405


def test_celery_queue_collector_adds_up_the_priority_lists():
    broker = MagicMock()
    channel = broker.__enter__.return_value.default_channel
    channel.sep = "\x06\x16"
    channel.priority_steps = [0, 3, 6, 9]
    lengths = {"celery": 2, "celery\x06\x163": 1}
    channel.client.llen.side_effect = lambda key: lengths.get(key, 0)

    with patch.object(app, "connection_for_read", return_value=broker):
        (gauge,) = list(CeleryQueueCollector().collect())

    assert gauge.name == "migrator_celery_queue_length"
    assert [(s.labels, s.value) for s in gauge.samples] == [({"queue": "celery"}, 3)]


def test_celery_queue_collector_yields_nothing_when_the_broker_fails():
    with patch.object(app, "connection_for_read", side_effect=OSError):
        assert not list(CeleryQueueCollector().collect())


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels) or 0


def test_record_migration_finished_counts_the_run():
    before = {
        "count": _sample(
            "migrator_migration_duration_seconds_count", {"status": "success"}
        ),
        "sum": _sample(
            "migrator_migration_duration_seconds_sum", {"status": "success"}
        ),
        "migrated": _sample("migrator_migrated_files_total"),
        "source": _sample("migrator_source_files_total"),
    }
    extra_task = SimpleNamespace(
        task_result=SimpleNamespace(date_created=timezone.now() - timedelta(minutes=2)),
        integrity_report={
            "summary": {"migrated_files_count": 3, "source_files_count": 4}
        },
    )

    record_migration_finished(extra_task, "success")

    assert (
        _sample("migrator_migration_duration_seconds_count", {"status": "success"})
        == before["count"] + 1
    )
    assert (
        _sample("migrator_migration_duration_seconds_sum", {"status": "success"})
        >= before["sum"] + 120
    )
    assert _sample("migrator_migrated_files_total") == before["migrated"] + 3
    assert _sample("migrator_source_files_total") == before["source"] + 4


def test_record_migration_finished_without_integrity_report():
    before = _sample("migrator_migrated_files_total")
    extra_task = SimpleNamespace(
        task_result=SimpleNamespace(date_created=timezone.now()),
        integrity_report={},
    )

    record_migration_finished(extra_task, "failure")

    assert _sample("migrator_migrated_files_total") == before


@pytest.mark.parametrize("authorization", [None, "Bearer wrong"])
def test_protect_refuses_the_worker_metrics_without_the_key(settings, authorization):
    settings.PROMETHEUS_API_KEY = "secret"
    wsgi_app = MagicMock()
    start_response = MagicMock()
    environ = {"HTTP_AUTHORIZATION": authorization} if authorization else {}

    assert protect(wsgi_app)(environ, start_response) == [b"Unauthorized"]

    start_response.assert_called_once_with(
        "401 Unauthorized", [("WWW-Authenticate", 'Bearer realm="metrics"')]
    )
    wsgi_app.assert_not_called()


def test_protect_serves_the_worker_metrics_with_the_key(settings):
    settings.PROMETHEUS_API_KEY = "secret"
    wsgi_app = MagicMock(return_value=[b"metrics"])
    environ = {"HTTP_AUTHORIZATION": "Bearer secret"}
    start_response = MagicMock()

    assert protect(wsgi_app)(environ, start_response) == [b"metrics"]
    wsgi_app.assert_called_once_with(environ, start_response)


@patch("core.metrics.make_server")
def test_worker_metrics_server_is_off_by_default(make_server, settings):
    settings.PROMETHEUS_METRICS_ENABLED = False

    start_worker_metrics_server()

    make_server.assert_not_called()


@patch("core.metrics.threading.Thread")
@patch("core.metrics.make_server")
def test_worker_metrics_server_starts_when_enabled(
    make_server, thread, settings, monkeypatch, tmp_path
):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    settings.PROMETHEUS_METRICS_ENABLED = True
    settings.PROMETHEUS_WORKER_METRICS_PORT = 9999

    start_worker_metrics_server(sender=MagicMock())

    assert make_server.call_args.args[:2] == ("", 9999)
    thread.assert_called_once_with(
        target=make_server.return_value.serve_forever, daemon=True
    )
    thread.return_value.start.assert_called_once()


def test_setup_prometheus_metrics_requires_a_key(monkeypatch):
    monkeypatch.setattr(Base, "PROMETHEUS_API_KEY", None)

    with pytest.raises(ValueError, match="PROMETHEUS_API_KEY"):
        Base.setup_prometheus_metrics()


def test_setup_prometheus_metrics_wires_django_prometheus(monkeypatch, tmp_path):
    multiproc_dir = tmp_path / "metrics"
    # Restored after the test, whatever setup_prometheus_metrics writes to them.
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "unset")
    monkeypatch.setattr(values, "ValueClass", values.ValueClass)
    monkeypatch.setattr(Base, "PROMETHEUS_API_KEY", "secret")
    monkeypatch.setattr(Base, "PROMETHEUS_MULTIPROC_DIR", str(multiproc_dir))
    monkeypatch.setattr(Base, "PROMETHEUS_METRICS_SSL_REDIRECT_EXEMPT", True)
    monkeypatch.setattr(Base, "SECURE_REDIRECT_EXEMPT", ["^__heartbeat__"])
    monkeypatch.setattr(Base, "INSTALLED_APPS", ["core"])
    monkeypatch.setattr(Base, "MIDDLEWARE", ["a", "b"])
    monkeypatch.setattr(
        Base,
        "DATABASES",
        {"default": {"ENGINE": "django.db.backends.postgresql_psycopg2"}},
    )
    monkeypatch.setattr(
        Base, "CACHES", {"default": {"BACKEND": "django_redis.cache.RedisCache"}}
    )

    Base.setup_prometheus_metrics()

    assert multiproc_dir.is_dir()
    assert Base.INSTALLED_APPS == ["core", "django_prometheus"]
    assert Base.MIDDLEWARE == [
        "core.metrics.PrometheusAuthMiddleware",
        "django_prometheus.middleware.PrometheusBeforeMiddleware",
        "a",
        "b",
        "django_prometheus.middleware.PrometheusAfterMiddleware",
    ]
    assert (
        Base.DATABASES["default"]["ENGINE"]
        == "django_prometheus.db.backends.postgresql"
    )
    assert (
        Base.CACHES["default"]["BACKEND"]
        == "django_prometheus.cache.backends.redis.RedisCache"
    )
    assert Base.SECURE_REDIRECT_EXEMPT == ["^__heartbeat__", "^metrics$"]
    assert os.environ["PROMETHEUS_MULTIPROC_DIR"] == str(multiproc_dir)
    assert values.ValueClass.__name__ == "MmapedValue"
