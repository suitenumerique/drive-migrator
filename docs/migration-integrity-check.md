# Migration integrity check

The integrity check records, for every source file of a migration run, how far
it went: retrieved from the source, written to the local disk, received by the
destination. When a file is missing on the destination, it tells at which step
it was lost.

## Enabling it

- Per workspace, in the Django admin: "Is file integrity tracked" set to
  **Yes** or **No**.
- For every workspace left on **Follow the feature flag**: create an active
  `file-integrity-tracking` feature flag. Without that flag, the check is off.

## Reading the results

In the Django admin, on each run (`Extra task infos`, or the runs listed on a
workspace page):

- **Integrity check passed**: green when no file was lost for an unexplained
  reason, red otherwise, unknown when the check did not run.
- **Files migrated**: `migrated/source` files, e.g. `5/7`, followed by
  `(N analyzing)` when Drive has not finished analyzing some files yet.
- The run detail page lists the 50 most severe files; the full listing can be
  downloaded as CSV.

## File states

Each source file gets one row per checked destination, or a single row when it
was lost before reaching the local disk. Only Drive is checked file by file;
the archive and Resana destinations are listed as "not checked".

| State | Meaning | Anomaly |
|---|---|---|
| `ok` | On the destination, ready and with the local size. A `file_too_large_to_analyze` file counts as ok: Drive keeps it downloadable, but leaves it out of folder ZIP exports and, for non-creators (every member in `service_account` mode), offers no online editing or preview and warns before download. Without checked destination: written to the local disk. | no |
| `truncated` | Dropped by `MIGRATION_FILE_LIMIT_PER_WORKSPACE`. | no |
| `download_failed` | The source download raised an error (see `error`). | no |
| `not_written` | The download reported no error but wrote no file. | **yes** |
| `not_attempted` | Never processed: the run stopped before this file. | only if the run succeeded |
| `not_created` | The destination has no item for this file (e.g. rejected by Drive). | only if the run succeeded |
| `pending` | Created on Drive, but its upload was never completed. | only if the run succeeded |
| `analysis_unfinished` | Received by Drive with the right size, malware analysis still running at the end of the check. | no |
| `not_ready` | Blocked by Drive: `suspicious` (malware found or analyzer error). A `suspicious` file is hidden from the workspace members. | **yes** |
| `size_mismatch` | Ready (or too large to analyze) on Drive, but with a size different from the local file. | **yes** |
| `unverified` | The destination could not be read back (see `errors` in the summary). | **yes** |
| `extra_on_destination` | On the destination without source file: the members CSV written by the migrator, or a duplicate. | no |

"Only if the run succeeded": a failed run already reports why it stopped, so
the files it did not finish are not counted as anomalies. In a successful run,
which goes through every file, they point to a bug.

## Known limit

A file still under analysis when the check ends (`analysis_unfinished`) is not
followed afterwards. If the Drive malware analyzer is down, such files can turn
`suspicious` after the run, while the run shows a passed check. Watch the
`(N analyzing)` count in the admin and `analysis_unfinished_count` in the
PostHog `migration_finished` event.
