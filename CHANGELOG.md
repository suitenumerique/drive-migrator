# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Show the linked workspace and migration user in the Celery task result
  admin list view

#### Dependencies

- Upgrade `django-rest-framework` to `3.18.2`

## [v0.11.0] - 2026-10-23

### Added

- Configure per destination whether users must create their account #270

### Fixed

- Retry transient API errors (timeouts, connection errors, 5xx) in the Osmose,
  Resana and Drive API clients #264
- Ask users without a Drive account to log in to Drive before migrating #266

## [v0.10.1] - 2026-10-22

### Added

- Log the Resana and Drive tokens lifetime before each migration and on refresh
- Send the application INFO logs to the console from the web process too,
  level set by `LOGGING_LEVEL_LOGGERS_APP` (libraries:
  `LOGGING_LEVEL_LOGGERS_ROOT`); Celery workers keep their `-l` level for
  tasks and libraries

### Changed

- Indicate that access transfer is not yet available in migration dialog

## [v0.10.0] - 2026-10-19

### Added

- Zip the files a destination refused and link it in the e-mail #257

### Fixed

- Skip files blocked in front of Drive instead of failing the run

## [v0.9.0] - 2026-10-14

### Added

- Attach a CSV of files that failed to migrate to the notification e-mail #256

### Changed

- Use Drive's resource server (external APIs) instead of frontend APIs

#### Dependencies

- Upgrade `Django` to `5.2.17`
- Upgrade `PyJWT` to `2.15.1`

### Fixed

- list locked Resana workspaces the user is Animateur of
- Refuse source files resolving out of their folder in the local workspace
- Reset previous workspace migration errors when a new migration starts #256

## [v0.8.10] - 2026-10-07

### Added

- setup Celery
- setup Flower
- setup S3 configuration
- add 404 page
- add debug toolbar
- add django-filters for API
- use redis as cache provider
- add Storybook
- add sass support
- setup Agent Connect auth
- add optional logout on 401 errors
- setup layout
- add custom button
- add badge
- setup models
- add demo data generation command
- add workspace factory
- add command to set workspaces status to none
- setup API
- add dev purpose APIs
- add Osmose backends
- add mail templates and manager
- add export task
- add workspace API
- add osmose synchronizing page
- add dashboard page
- add exporting configuration pages
- setup admin interface
- add optional osmose backend debug files
- add helm files for local and staging envs
- implement basic Resana API
- add optional setting to cap migrated files per workspace.
- add MFA/OTP support to Resana Keycloak login flow
- add migration user email/name columns and search to workspace admin #143
- add admin actions to reset a user's Resana or Drive connection #198
- add PostHog events for login, sync and migration outcome
- add opt-in per-file migration integrity report #235
- show migrated/source file counts and gaps on workspace cards #235
- lock Resana workspace and grant folder access during migration #215
- add Drive status and root folder id to the workspace admin CSV export
- add workspace size and file types to PostHog migration_finished event

### Changed

- show ProConnect logo on connect page for zip target
- update the name of LaSuite Drive in mail
- update the logo in mail header
- add tooltip for cropped title
- update zip archive download journey
- specify logo fichiers
- derive DRIVE_API_BASE_URL and DRIVE_FRONTEND_URL from DRIVE_BASE_URL #103
- update cunningham tokens
- replace unmaintained retry package with tenacity
- rename the members CSV of archive and Drive exports to
  users_list_by_migrator.csv, so it no longer overwrites a source file #235
- switch Resana source auth to PKCE + bridge flow #190

### Fixed

- patch remaining frontend dependency vulnerabilities
- patch remaining mail dependency vulnerabilities
- patch Handlebars and js-yaml vulnerabilities in the API client generator
- remove vulnerable legacy tooling and crypto polyfills from Storybook
- fix illustration page finish
- fix CI checks (self-hosted runner deps, gitlint job, test-back env vars)
- fix Resana access token refresh crashing with KeyError: 'access_token'
- fix HTML entities not decoded in Resana workspace/folder/file names
- fix standalone bootstrap (OIDC endpoint, CSRF) and update README #105
- secure archive ZIP download link
- fix export crash on folder/file names containing "/" #165
- fix lint-git print() check matching removed lines instead of only added ones
- fix error modal looping infinitely on connect page #138
- use a more complete Resana API endpoint to list workspace users #213
- retry Drive upload-ended notification on transient network timeouts #176
- retry Drive API calls on transient 5xx errors instead of failing #208
- retry Resana file download and skip failing files instead of aborting #175
- skip and list files refused by Drive instead of failing the migration
- declare the file size when creating Drive items, required by recent Drive
- exclude Resana workspaces where user isn't Animateur from migration #110 #160
- exclude Resana personal workspaces from migration by default #163
- fix user.csv not generated for locked Resana workspaces #169
- fix accented filenames mangled in workspace ZIP export #166
- pull MinIO images from Chainguard since quay.io pulls are gated
- share migrated Drive workspaces with the exact member email, not a lookalike

### Removed

- delete unused var in Makefile

[unreleased]: https://github.com/suitenumerique/drive-migrator/compare/v0.11.0...main
[v0.11.0]: https://github.com/suitenumerique/drive-migrator/releases/v0.11.0
[v0.10.1]: https://github.com/suitenumerique/drive-migrator/releases/v0.10.1
[v0.10.0]: https://github.com/suitenumerique/drive-migrator/releases/v0.10.0
[v0.9.0]: https://github.com/suitenumerique/drive-migrator/releases/v0.9.0
[v0.8.10]: https://github.com/suitenumerique/drive-migrator/releases/v0.8.10
