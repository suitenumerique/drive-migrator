# Prometheus metrics

The backend ships an opt-in instrumentation based on
[django-prometheus](https://github.com/django-commons/django-prometheus). It is
off by default: without `PROMETHEUS_METRICS_ENABLED`, nothing is installed and
`/metrics` is a 404. The Development configuration turns it on with the key
`dev-metrics-key`.

## Enabling it

```bash
PROMETHEUS_METRICS_ENABLED=True
PROMETHEUS_API_KEY=<a long random secret>
```

The application refuses to start with the metrics enabled and no key. Set the
same variables on the web pods and on the Celery workers.

| Variable | Default | Role |
|---|---|---|
| `PROMETHEUS_METRICS_ENABLED` | `False` | Turn the metrics on |
| `PROMETHEUS_API_KEY` | none | Bearer token required on every scrape |
| `PROMETHEUS_METRICS_SSL_REDIRECT_EXEMPT` | `False` | Let `/metrics` be scraped over plain http where `SECURE_SSL_REDIRECT` is on |
| `PROMETHEUS_MULTIPROC_DIR` | `<tmp>/migrator-prometheus-<uid>` | Where the processes of one pod write their numbers |
| `PROMETHEUS_WORKER_METRICS_PORT` | `8001` | Port of the Celery worker endpoint |

## Scraping

Two endpoints, both requiring `Authorization: Bearer <PROMETHEUS_API_KEY>`:

- web pods: `GET /metrics` on the Django port. It is outside of `/api/`, so the
  application ingress does not publish it.
- Celery workers: `GET /` on `PROMETHEUS_WORKER_METRICS_PORT`, served by the
  main worker process.

Scrape each pod directly (`PodMonitor` or `ServiceMonitor`). A pod is reached
over plain http, past the proxy terminating TLS: set
`PROMETHEUS_METRICS_SSL_REDIRECT_EXEMPT=True` on the web pods, otherwise the
Production settings redirect the scrape to https.

```yaml
scrape_configs:
  - job_name: migrator-backend
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/migrator-api-key
    static_configs:
      - targets: ["backend:8000"]
  - job_name: migrator-celery
    metrics_path: /
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/migrator-api-key
    static_configs:
      - targets: ["celery:8001"]
```

gunicorn workers and Celery children all write to `PROMETHEUS_MULTIPROC_DIR`,
and whichever process answers adds them up. The directory must be local to the
pod and is not shared between replicas.

## What is measured

Web pods:

- HTTP requests by view, method and status (`django_http_*`).
- SQL queries and errors (`django_db_*`), cache hits and misses (`django_cache_*`).
- Read from the database at each scrape:
  - `migrator_workspaces{status}`: workspaces by global status.
  - `migrator_workspace_destinations{destination,status}`: workspaces by
    destination and export status, e.g. pending or failed Drive migrations.
  - `migrator_workspaces_truncated`, `migrator_workspaces_with_download_errors`,
    `migrator_workspaces_with_upload_errors`: as of their last migration.
  - `migrator_integrity_checks{passed}`: runs by integrity check outcome
    (`true`, `false`, `unknown`).
- `migrator_celery_queue_length{queue}`: tasks waiting on the Celery queue,
  asked to Redis at each scrape.

These gauges describe the whole deployment: every web replica reports the same
value. Read them with `max`, never `sum`.

Celery workers:

- `migrator_migration_duration_seconds{status}`: histogram of the finished
  migrations, from queueing to the end of the task. Its `_count` is the number
  of migrations by status.
- `migrator_migrated_files_total`, `migrator_source_files_total`: files of the
  integrity-tracked runs.
- `migrator_download_errors_total`: files that failed to download from the
  source.
- `migrator_drive_upload_rejections_total{reason}`: files refused by Drive, by
  Drive error code.
- SQL queries made by the tasks (`django_db_*`).

No label carries a path, a user or a workspace identifier.
