# Source and destination backends

A migration reads a workspace from one source and sends it to several
destinations. Each is a backend class, loaded from the settings:

| Setting | Holds | Implementations |
| --- | --- | --- |
| `SOURCE_BACKEND` | One `AbstractSourceBackend` | `core/sources/osmose`, `core/sources/resana`, `core/sources/filesystem.py` |
| `DESTINATION_BACKENDS` | A list of `AbstractDestinationBackend` | `core/destinations/archive`, `core/destinations/resana`, `core/destinations/drive` |

Both contracts live in `core/backends/`. How the tasks of a migration call
them is described in [migration-pipeline.md](migration-pipeline.md).

Backends are instantiated without arguments: a source for each use, a
destination once per process (`DestinationRegistry` caches it). Whatever
belongs to a run therefore goes to the run, not to the instance.

## Destinations

A destination receives a run file by file, in four calls, each one possibly
in a different Celery task and on a different worker:

1. `open_export(user, run)`, once, in the task starting the run: prepare
   the destination, e.g. create the folders.
2. `put_file(user, run, migration_file, local_path)`, once per downloaded
   file, in the task of that file.
3. `close_export(user, run)`, once every file is done, in the finish of the
   run: generated files, sharing, mail, destination status.
4. `abort_export(run)`, instead of the closing, when the run or the
   destination failed: best effort, keep a trace of what the export did.

`core.processing.file_exports.FileExports` makes these calls, so that a
destination failing never stops the others.

### Sending a file

`put_file` records what happened to the file in
`migration_file.results[self.name]`, a dict with a `FileExportStatus` as
`"status"`, plus whatever the closing will need (an S3 key, a Drive item id,
an `"error"`):

| Status | Meaning |
| --- | --- |
| `SENT` | The destination holds the file. |
| `REJECTED` | The destination refused the file itself, e.g. its type or size. |
| `FAILED` | The file was lost for another reason, e.g. a network error. |

A file that is not `SENT` is kept on S3 for the user (see below), and the run
goes on with its next files.

Raise only when the destination itself is unusable, e.g. a revoked token: the
destination then gets no more files, and fails when the run closes. So does
a destination with `MIGRATION_DESTINATION_MAX_CONSECUTIVE_FAILURES` files
`FAILED` in a row; `REJECTED` files do not count, they say nothing about the
destination.

A database error (`OperationalError`, `InterfaceError`) raised from
`put_file` is not held against the destination: the whole file task is
retried.

### State shared by the tasks

The tasks of a run share nothing but the database. What the next calls need,
e.g. the ids of the folders `open_export` created, goes to
`run.destinations_state[self.name]`, saved with an `UPDATE` of that field
only. `put_file` and `close_export` read it back from `run`.

`run.exports_state` belongs to `FileExports` (error, failures in a row,
closing of each destination): leave it alone.

### Running twice

A task may run again: delivered twice by the broker, or queued again by the
watchdog after a worker died. A destination must allow it:

- `put_file` is not called again for a file already `SENT` to that
  destination, but may be called again for a file whose first sending was cut
  short. The Drive backend, for one, gets back the item a former attempt
  created instead of creating a second one.
- A closing cut short is not run a second time: the destination is aborted
  instead. `close_export` therefore saves early what `abort_export` must not
  redo (Drive saves `lost_files_archived`).

### Closing

`close_export` reads the results of every file from `run.files`, then must
set `workspace.destination_statuses[self.name]`, through
`workspace.set_destination_status()`, and save the workspace:

- `SUCCESS` when the destination is done;
- `PENDING` when it goes on alone, e.g. the Resana import job, whose outcome
  `poll_completion()` reads later.

Raising from `close_export` fails the destination: it is aborted, and the
run fails once the other destinations are closed.

The archive and Drive backends send their mail from `close_export`, through
`MailsManager().send_migration_mail()`.

### Files the destination did not take

When a destination does not take a file (`REJECTED` or `FAILED`), the file
task keeps a copy on S3 before deleting the local one
(`core.processing.rejected_files.keep_refused_file`). The destination then
hands these files back to the user, as a zip linked from its mail:

```python
RejectedFilesArchive(run.workspace, self).build(run, rejected)
```

with one `RejectedFile(path, code, item_id)` per file, its `code` explained
to the user by `describe_rejection()`. Do it in `close_export`, before the
mail, and in `abort_export` too when the user should get them even after a
failure (Drive does). The copies no zip lists are deleted with the run.

### Other hooks

| Method | Default | Purpose |
| --- | --- | --- |
| `describe_rejection(code)` | "Fichier refusé par {label}" | Reason of a `RejectedFile` code, shown to the user. |
| `get_account_policy()` | `AccountPolicy.NONE` | Whether migrating needs a user account (`AUTO_CREATED`, `USER_CREATED`). |
| `user_account_exists(user)` | `True` | Asked under `USER_CREATED` before the migration. |
| `get_account_creation_url()` | `None` | Where the user creates that account. |
| `check_integrity(workspace, run, wait_for_analysis)` | Not implemented | Re-read what the destination holds, see [migration-integrity-check.md](migration-integrity-check.md). |
| `get_download_url`, `get_error_details`, `retry`, `poll_completion` | Not implemented | Used by the API for some destinations only. |

### Skeleton

```python
class ExampleDestinationBackend(AbstractDestinationBackend):
    name = "example"  # Key in Workspace.destination_statuses: never change it.
    label = "Example"

    def open_export(self, user, run) -> None:
        folder_id = create_root_folder(run.workspace.title)
        run.destinations_state[self.name] = {"folder_id": folder_id}
        MigrationRun.objects.filter(pk=run.pk).update(
            destinations_state=run.destinations_state
        )

    def put_file(self, user, run, migration_file, local_path: str) -> None:
        state = run.destinations_state[self.name]
        try:
            upload(local_path, state["folder_id"], migration_file.path)
        except NetworkError as error:
            migration_file.results[self.name] = {
                "status": FileExportStatus.FAILED,
                "error": str(error),
            }
            return
        migration_file.results[self.name] = {"status": FileExportStatus.SENT}

    def close_export(self, user, run) -> None:
        workspace = run.workspace
        lost = [
            RejectedFile(row.path, "upload_failed")
            for row in run.files.iterator()
            if row.results.get(self.name, {}).get("status")
            not in (None, FileExportStatus.SENT)
        ]
        RejectedFilesArchive(workspace, self).build(run, lost)
        workspace.set_destination_status(self.name, Workspace.Status.SUCCESS)
        workspace.save()
```

Then add its dotted path to `DESTINATION_BACKENDS`.

## Sources

A source lists the workspaces of a user, reads the tree of one, and
downloads its files:

| Method | Called | Purpose |
| --- | --- | --- |
| `get_workspaces(user)` | By the synchronize endpoint | The workspaces the user can migrate. |
| `begin_export(workspace)` | Once, when the run starts, before anything is read | Freeze the workspace, grant the migration account the access it lacks. |
| `get_workspace_structure(workspace)` | Once, when the run starts | The tree of `SourceFolder` and `SourceFile`, its root virtual. |
| `prepare_export(workspace, local_folder_path)` | Once, before the destinations open | Fill `workspace.members` (`name`, `firstName`, `email`), for the destinations to share or list. |
| `resume_export(workspace)` | In every later task of the run (each file, the finish) | Set up only what `download_file` needs in a fresh worker, e.g. a session. |
| `download_file(file, destination_path)` | Once per file, in its own task | Write the file to the local disk, retrying transient errors itself. |
| `finalize_export(workspace)` | Once, when the run ends, whatever its outcome | Undo what `begin_export` did. Must not raise: log instead. |

`begin_export` and `finalize_export` run in different tasks, likely on
different workers: what the second must undo goes to the workspace (Resana
keeps it in `Workspace.source_lock_state`).

A `SourceFile` carries an opaque `download_url`, handed back to
`download_file`, and the `size` the source announces, used to cut the run
into batches; `None` when the source does not tell.

Raise `SourceTokenExpired` when the user's token can no longer be used or
refreshed: the run stops, the files left are skipped, and the user is asked to
log in again. Any other error raised by `download_file` only fails that file.
