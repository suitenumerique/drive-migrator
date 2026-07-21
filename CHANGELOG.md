# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Changed

- update cunningham tokens

### Fixed

- fix CI checks (self-hosted runner deps, gitlint job, test-back env vars)
- fix Resana access token refresh crashing with KeyError: 'access_token'
- fix HTML entities not decoded in Resana workspace/folder/file names
- fix standalone bootstrap (OIDC endpoint, CSRF) and update README #105
- secure archive ZIP download link

### Removed

- delete unused var in Makefile


[unreleased]: https://github.com/numerique-gouv/impress/main
