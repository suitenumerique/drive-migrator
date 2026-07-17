# Drive migration tool
A tool to migrate files and folder structures between document storage solutions. Designed as a building block for cross-instance interoperability within [LaSuite](https://lasuite.numerique.gouv.fr) and the broader [OpenBuro](https://openburo.eu) ecosystem.

<img src="/docs/drive-migrator-schema.png" width="65%" align="center"/>

This implementation currently focuses on Resana → LaSuite Drive migrations, but the architecture supports other source/target configurations that will be added as the project grows.

# Table of Contents

- [Why this tool](#why-this-tool)
- [Features](#features)
- [Get started](#get-started)
- [Philosophy](#philosophy)
- [Contributing](#contributing)
- [License](#license)

## Why this tool
Public administrations rely on a wide variety of document storage and sharing solutions (Resana, Nextcloud, SharePoint, [LaSuite Drive](https://github.com/suitenumerique/drive), etc.). Interoperability between these systems is a prerequisite for **reversibility**: organizations must remain free to switch provider without losing access to their data.

This tool makes Drive-to-Drive migrations straightforward: it reads a source Drive instance via API, preserves folder hierarchies and metadata, and writes everything to a target Drive instance.

It is a concrete implementation of the cross-app data portability pillar defined by [OpenBuro](https://openburo.eu).


## Features
* Migrate full folder hierarchy from the source document tree
* Migrate all files, including duplicates (with disambiguation)
* Workspace members migration
* Configurable permission mapping between source and target instances
* Rate-limiting and retry logic for large datasets
* Asynchronous (email notification)

### What is out of the migration scope

* Empty folders (files must be present for a folder to be carried over)
* File metadata: versions, comments, likes — only the latest version of each file is transferred
* Other workspace content: wikis, calendars, discussion boards, project tasks


## Get started

This section covers running the migrator in **standalone mode**, with its own bundled Keycloak
and MinIO instances. It is the quickest way to try the tool locally.

> A second setup lets you run this tool alongside a local [LaSuite
> Drive](https://github.com/suitenumerique/drive) instance instead of the bundled
> one. That configuration will be documented separately.

### Prerequisite

Make sure you have a recent version of Docker and [Docker
Compose](https://docs.docker.com/compose/install) installed on your laptop:

```bash
$ docker -v
  Docker version 25.0.2, build 29cf629

$ docker compose version
  Docker Compose version v2.24.5
```

> ⚠️ You may need to run the following commands with `sudo` but this can be
> avoided by assigning your user to the `docker` group.

### Project bootstrap

The easiest way to start working on the project is to use GNU Make:

```bash
$ make bootstrap FLUSH_ARGS='--no-input'
```

Then you can access to the project in development mode by going to http://localhost:3000.
You will be prompted to log in, the default credentials are:
```bash
username: impress
password: impress
```
---

This command builds the `app` container, installs dependencies, performs
database migrations and compile translations. It's a good idea to use this
command each time you are pulling code from the project repository to avoid
dependency-releated or migration-releated issues.

Your Docker services should now be up and running 🎉

Note that if you need to run them afterwards, you can use the eponym Make rule:

```bash
$ make run-frontend-dev
```

### Adding content

You can create a basic demo site by running:

    $ make demo

Finally, you can check all available Make rules using:

```bash
$ make help
```

### Django admin

You can access the Django admin site at
[http://localhost:8071/admin](http://localhost:8071/admin).

You first need to create a superuser account:

```bash
$ make superuser
```

## Philosophy

This tool is part of LaSuite's contribution to the [OpenBuro](https://openburo.eu) standard, an emerging European initiative that defines open standards for workplace app interoperability. One of its core pillars is **cross-app data portability**: business objects (documents, files) should flow between services with their context preserved. Users and organizations must be free to move between services without losing their files or being tied to a single provider.

The long-term ambition is a generic migration tool that works across any document storage solution. **We're not there yet** — currently supported platforms are Resana, LaSuite Fichiers, and Osmose. 

Contributions to add new integrations are very welcome.


## Contributing

We welcome contributions of any kind — bug reports, feature requests, documentation improvements, and pull requests. 

* Open a PR — see [CONTRIBUTING.md](./CONTRIBUTING.md) for development setup
* Submit a [feature request](https://github.com/suitenumerique/drive-migrator/issues/new?template=Feature_request.md) or [bug report](https://github.com/suitenumerique/drive-migrator/issues/new?template=Bug_report.md)

This project is community-driven. Don't hesitate to get in touch if you have questions about implementation or design decisions.

**Contributions that extend support to other source or target platforms are particularly welcome — the architecture is designed for it.**


## License

This work is released under the MIT License (see [LICENSE](./LICENSE)).
