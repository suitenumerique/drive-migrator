# Migration pipeline

A migration runs as several Celery tasks, so that the worker disk only holds
the file being transferred, and so that migrations started together share the
workers.

- `export` plans the run: it locks the source workspace, reads its tree,
  stores one row per file (`MigrationRun`, `MigrationFile`), opens the
  destinations, then queues the first batch of files.
- `export_file`, one per file: downloads the file, hands it to the
  destinations, then deletes the local copy.
- `finish_migration`, once every file is done: closes the destinations,
  purges the rows of the run, releases the source workspace, then records the
  outcome and tells the user.

Only `export` gets a row in the Django admin (`Task results`). It stays
`STARTED` while the run goes on, until `finish_migration` sets its status,
date and error.

## Batches

The file tasks of a run are queued one batch at a time: the last file of a
batch queues the next batch. A migration started after another one then gets
the workers between two batches, instead of waiting for the whole run.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MIGRATION_BATCH_MAX_FILES` | `100` | Files in a batch at most. |
| `MIGRATION_BATCH_MAX_BYTES` | `1073741824` (1 GiB) | Bytes in a batch at most, from the sizes the source announces (Resana rounds them to 0.01 MiB). |

`0` means no limit; both at `0` queue the whole run at once. A batch always
holds at least one file, even one bigger than `MIGRATION_BATCH_MAX_BYTES`.

## Redis visibility timeout

With the Redis broker, a message a worker has taken but not acknowledged yet
goes back to the queue after the `visibility_timeout`, 1 hour for Redis. A
worker reserves its next message while it runs a task: when that task lasts
longer than the timeout, the reserved message is delivered again, and the same
file is exported twice. A second copy of an export, or of a file task whose
file is done, is skipped; a file task still running is not, and its file is
sent twice at once.

The timeout defaults to 12 hours here. Keep it above the longest task, i.e.
the transfer of the biggest file or the planning of the biggest workspace;
setting the variable replaces the whole dictionary:

```
DJANGO_CELERY_BROKER_TRANSPORT_OPTIONS={"visibility_timeout": 43200}
```

The other side: a message reserved by a worker killed outright (out of
memory, node lost) waits for the whole timeout before another worker takes
it. A file task is queued again sooner by the watchdog, but a migration not
started yet starts that much later. A worker stopped gracefully hands its
reserved messages back at once.

## Expired source token

When the source token expires during a run, the run stops: the files already
queued are skipped, no further batch is queued, and the migration fails asking
the user to log in to the source again.

## Known limit

A worker killed in the middle of a file task (out of memory, node lost) leaves
its run unfinished: the run waits for a file that never ends, and the source
workspace stays locked.
