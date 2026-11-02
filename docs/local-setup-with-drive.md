# Local setup with La Suite Drive

Step-by-step guide to run the migrator and a local Drive side by side, sharing
Drive's Keycloak. At the end, you migrate a test workspace to Drive.

The source is the local filesystem, so no Resana or Osmose access is needed.

Follow the steps in order: Drive comes first because it provides Keycloak and
the Docker network.

| Service | URL | Login |
|---|---|---|
| Drive frontend | http://localhost:3000 | Keycloak user |
| Drive API | http://localhost:8071 | |
| Migrator frontend | http://localhost:3010 | Keycloak user |
| Migrator API and admin | http://localhost:8072/admin | `admin@example.com` / `admin` |
| Keycloak console | http://localhost:8083 | `admin` / `admin` |
| Migrator mails | http://localhost:1082 | |

## Step 1: clone both repositories

```bash
git clone git@github.com:suitenumerique/drive.git
git clone git@github.com:suitenumerique/drive-migrator.git
```

Docker and Docker Compose are required. These ports must be free:

- Drive: 1081, 3000, 4444, 6379, 6433, 6434, 8071, 8080, 8083, 9000, 9001,
  9980, 9981
- Migrator: 1082, 3010, 5434, 5555, 8072, 8085, 8086, 9010, 9011, 15433, 16380

## Step 2: configure Drive

All the commands of this step run in the `drive` repository.

In `env.d/development/common`, delete the `AWS_S3_DOMAIN_REPLACE` line and keep
`AWS_S3_ENDPOINT_URL` as is. Add this line:

```bash
AWS_S3_UPLOAD_ACL=default
```

In `compose.yaml`, add this to the `rustfs` service, so that the migrator
reaches it:

```yaml
    networks:
      - default
      - lasuite
```

Create `env.d/development/common.local`, which enables the external API used
by the migrator:

```bash
cat > env.d/development/common.local <<'EOF'
OIDC_RESOURCE_SERVER_ENABLED=True
OIDC_RS_ALLOWED_AUDIENCES=osmose-migration
EXTERNAL_API={"items": {"enabled": True, "actions": ["create", "list", "retrieve", "destroy", "children", "upload_ended"]}, "item_access": {"enabled": True, "actions": ["create"]}, "item_invitation": {"enabled": True, "actions": ["create"]}, "users": {"enabled": True, "actions": ["get_me", "list"]}}
EOF
```

## Step 3: start Drive

```bash
make bootstrap
```

Check that http://localhost:3000 opens and that you can log in with `drive` /
`drive`.

## Step 4: create the migrator client in Keycloak

1. Open http://localhost:8083 and log in with `admin` / `admin`. On the first
   login, Keycloak asks for a first and last name: enter anything. Port 8080
   does not work for the console.
2. Select the **drive** realm in the realm list.
3. Go to **Clients**, then **Create client**.
4. **Client ID**: `osmose-migration`, then **Next**.
5. Turn **Client authentication** on, keep **Standard flow** checked, then
   **Next**.
6. Fill in:
   - **Valid redirect URIs**: `http://localhost:8072/*`
   - **Valid post logout redirect URIs**: `http://localhost:3010/*`
   - **Web origins**: `http://localhost:3010`
7. Click **Save**.
8. In the **Credentials** tab, copy the **Client secret**. You need it at step 6.

> `make down` in Drive deletes the Keycloak data and the Drive database. Use
> `make stop` day to day. After a `make down`, see [Daily use](#daily-use).

## Step 5: create the user accounts

The realm already has test users. You can use them as is:

| Username | Email | Password |
|---|---|---|
| `drive` | `drive@drive.world` | `drive` |
| `paige` | `paige.turner@library.book` | `pass` |
| `miles` | `miles.ahead@roadmap.fwd` | `pass` |

To create another one, still in the **drive** realm:

1. Go to **Users**, then **Add user**.
2. Fill in **Username**, **Email**, **First name** and **Last name**, turn
   **Email verified** on, then
   **Create**. The email is required: it identifies the user in both apps.
3. In the **Credentials** tab, click **Set password**, enter a password, turn
   **Temporary** off, then **Save**.

Then log in once to http://localhost:3000 with each user who will migrate.
The migrator refuses users with no Drive account.

## Step 6: configure the migrator

All the commands of this step run in the `drive-migrator` repository.

Apply the Docker changes (other ports, `lasuite-network` network):

```bash
git apply docs/local-setup-with-drive.patch
mkdir -p data/workdir data/demo-data
```

In `src/frontend/apps/main/.env.development`, set:

```bash
NEXT_PUBLIC_API_ORIGIN=http://localhost:8072
```

Do not commit these changes, nor the ones made to Drive at step 2.

Create the env file:

```bash
cp env.d/development/common.dist env.d/development/common
```

In `env.d/development/common`, replace the sections below with these values.
Set `OIDC_RP_CLIENT_SECRET` to the secret from step 4.

```bash
# Migration source/destination
SOURCE_BACKEND=core.sources.filesystem.FileSystemSourceBackend
DESTINATION_BACKENDS=core.destinations.archive.backend.ArchiveDestinationBackend,core.destinations.drive.backend.DriveDestinationBackend
APP_WORK_DIR=/workdir

# Media
AWS_S3_ENDPOINT_URL=http://minio:9000
AWS_S3_ACCESS_KEY_ID=dinum
AWS_S3_SECRET_ACCESS_KEY=password
AWS_STORAGE_BUCKET_NAME=impress-media-storage
AWS_S3_DOWNLOAD_URL=http://localhost:9010

# OIDC
OIDC_OP_JWKS_ENDPOINT=http://drive-nginx-1:8083/realms/drive/protocol/openid-connect/certs
OIDC_OP_AUTHORIZATION_ENDPOINT=http://localhost:8080/realms/drive/protocol/openid-connect/auth
OIDC_OP_TOKEN_ENDPOINT=http://drive-nginx-1:8083/realms/drive/protocol/openid-connect/token
OIDC_OP_USER_ENDPOINT=http://drive-nginx-1:8083/realms/drive/protocol/openid-connect/userinfo
OIDC_OP_LOGOUT_ENDPOINT=http://localhost:8080/realms/drive/protocol/openid-connect/logout

OIDC_RP_CLIENT_ID=osmose-migration
OIDC_RP_CLIENT_SECRET=<secret from step 4>
OIDC_RP_SIGN_ALGO=RS256
OIDC_RP_SCOPES="openid email"

LOGIN_REDIRECT_URL=http://localhost:3010
LOGIN_REDIRECT_URL_FAILURE=http://localhost:3010
LOGOUT_REDIRECT_URL=http://localhost:3010

OIDC_REDIRECT_ALLOWED_HOSTS=["http://localhost:8080", "http://localhost:3010"]
OIDC_AUTH_REQUEST_EXTRA_PARAMS={}

# La Suite Drive
DRIVE_BASE_URL=http://host.docker.internal:8071
DRIVE_FRONTEND_URL=http://localhost:3000
DRIVE_AUTH_MODE=user_token
```

Generate the token encryption key and set it as `OIDC_TOKENS_ENCRYPTION_KEY`
in the same file:

```bash
docker run --rm python:3.13-slim sh -c \
  "pip install -q cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```

## Step 7: start the migrator

```bash
make bootstrap FLUSH_ARGS='--no-input'
```

Open http://localhost:3010, click **Start migration** and log in with `drive` /
`drive`. The migrator also starts its own Keycloak on port 8085: it is not used.

## Step 8: add test data

The workspaces of a user are the folders of `data/demo-data/<user email>/`:

```bash
mkdir -p data/demo-data/drive@drive.world/Test-workspace/Folder
echo "Hello" > data/demo-data/drive@drive.world/Test-workspace/hello.txt
echo "World" > data/demo-data/drive@drive.world/Test-workspace/Folder/world.txt
```

The list only synchronizes by itself while it is empty. To see a workspace
added later, open http://localhost:8072/api/v1.0/synchronize/ while logged in,
then reload the list.

On a fresh database the `read-only-mode` flag counts as active, which stops the
synchronization. Disable it:

```bash
docker compose exec -T app-dev uv run python manage.py shell -c "
from core.models import FeatureFlag
FeatureFlag.objects.update_or_create(name='read-only-mode', defaults={'is_active': False})"
```

## Step 9: run a migration

1. Open http://localhost:3010/dashboard/, not the home page: its connection
   page requires Resana. `Test-workspace` appears in the list.
2. Select it, click **Migrer**, **Continuer**, then **Start migration**. The
   destination is Drive.
3. Follow the worker logs: `docker compose logs -f celery-dev`.
4. Check the end-of-migration mail on http://localhost:1082.
5. Open http://localhost:3000: the workspace is there, with its files.

To migrate the same workspace again, open it in the admin
(http://localhost:8072/admin), set `Status` to `NONE`, `Destination statuses`
and `Destination metadata` to `{}`, then save. A failed workspace can instead
be relaunched with the **Retry failed** button.

## Daily use

```bash
# In drive
make run

# In drive-migrator
make run-frontend-dev
```

Start Drive first: the migrator needs its Keycloak and network. Stop with
`make stop` in both repositories.

After a `make down` in Drive, its database and the Keycloak data are gone: run
`make migrate` in Drive, then redo steps 4 and 5 and update
`OIDC_RP_CLIENT_SECRET`.

## Known limit

With the S3 settings of step 2, browsing and downloading work in Drive, but
uploading from its own frontend does not: the upload URL targets `rustfs:9000`,
which a browser cannot reach.
