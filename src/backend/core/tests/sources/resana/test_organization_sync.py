"""Portal organization calls and the tasks that store them."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.backends.source import (
    AbstractSourceBackend,
    SourceFolder,
    SourceManager,
    SourceWorkspace,
)
from core.factories import OrganizationFactory, UserFactory, WorkspaceFactory
from core.models import Organization, Workspace
from core.sources.resana.backend import ResanaSourceBackend
from core.sources.resana.organization_sync import sync_administered_organizations
from core.sources.resana.resana_members_client import (
    ResanaMembersClient,
    ResanaPortalError,
)

pytestmark = pytest.mark.django_db


def _client():
    with patch("core.sources.resana.resana_members_client.RetryingSession"):
        client = ResanaMembersClient(
            access_token="tok",
            session_id="sess",
            csrf_token="csrf",
            base_url="https://resana.example",
        )
    client.session = MagicMock()
    return client


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_get_workspaces_reads_the_organization_and_skips_personal_ones(settings):
    """The GED organization is kept for a shared space and dropped for a personal one."""
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"
    settings.RESANA_WEB_ENDPOINT = "https://resana.example"
    settings.RESANA_MIGRATE_PERSONAL_WORKSPACES = True
    raw_workspaces = [
        {
            "uuid": "ws-1",
            "name": "Espace Projet",
            "isPersonalWorkspace": False,
            "organization": {"id": "12", "name": "Mair&amp;ie"},
        },
        {
            "uuid": "ws-me",
            "name": "Mon espace",
            "isPersonalWorkspace": True,
            "organization": {"id": "12", "name": "Mairie"},
        },
    ]

    with patch(
        "core.sources.resana.backend.ResanaTokenManager"
    ) as token_manager, patch(
        "core.sources.resana.backend.InterstisClient"
    ) as ged, patch("core.sources.resana.backend.ResanaMembersClient") as members:
        token_manager.return_value.get_valid_token.return_value = "tok"
        ged.return_value.get_workspaces.return_value = raw_workspaces
        members.return_value.get_workspaces_with_role.return_value = [
            {"slug": "slug-1", "name": "Espace Projet", "role_code": "GESTIONNAIRE"}
        ]
        members.return_value.get_workspaces.return_value = [
            {"slug": "slug-1", "name": "Espace Projet", "uuid": "ws-1"}
        ]
        members.return_value.get_locked_workspaces.return_value = []
        result = ResanaSourceBackend().get_workspaces(MagicMock())

    by_id = {workspace.id: workspace for workspace in result}
    assert by_id["ws-1"].organization_id == "12"
    assert by_id["ws-1"].organization_name == "Mair&ie"
    assert by_id["ws-me"].organization_id is None


def test_administered_organizations_page_until_a_short_page():
    """offset/limit walks the list and stops on a page shorter than the limit."""
    client = _client()
    pages = {
        0: [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}],
        2: [{"id": "3", "name": "C"}],
    }

    def post(_url, data, **_kwargs):
        return _response({"tabPerimetres": pages[data["offset"]]})

    client.session.post.side_effect = post
    with patch("core.sources.resana.resana_members_client._PAGE_SIZE", 2):
        result = client.get_administered_organizations()

    assert [item["id"] for item in result] == ["1", "2", "3"]
    first = client.session.post.call_args_list[0]
    assert first.args[0].endswith("/public/perimetre/getPerimetre")
    assert first.kwargs["data"]["admin"] == "true"
    assert first.kwargs["data"]["licence"] == "true"


def test_portal_error_object_is_a_failure():
    """HTTP 200 with an error object is a refusal, not an empty list."""
    client = _client()
    client.session.post.return_value = _response(
        {"error": {"message": ["Vous n'avez pas le droit"]}}
    )
    with pytest.raises(ResanaPortalError):
        client.get_administered_organizations()


def test_repeated_page_does_not_loop():
    """A portal that ignores offset must not be requested forever."""
    client = _client()
    client.session.post.return_value = _response(
        {"tabPerimetres": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]}
    )
    with patch("core.sources.resana.resana_members_client._PAGE_SIZE", 2):
        result = client.get_administered_organizations()
    assert [item["id"] for item in result] == ["1", "2"]
    assert client.session.post.call_count == 2


def test_sync_administered_organizations_upserts_and_drops_stale_links(settings):
    settings.RESANA_WEB_ENDPOINT = "https://resana.example"
    user = UserFactory()
    other = UserFactory()
    kept = OrganizationFactory(source_id="12", name="Old")
    kept.admins.add(user)
    gone = OrganizationFactory(source_id="99", name="Gone")
    gone.admins.add(user, other)

    with patch(
        "core.sources.resana.organization_sync.ResanaTokenManager"
    ) as token_manager, patch(
        "core.sources.resana.organization_sync.ResanaMembersClient"
    ) as members:
        token_manager.return_value.get_valid_token.return_value = "tok"
        token_manager.return_value.get_session_id.return_value = "sess"
        token_manager.return_value.get_csrf_token.return_value = "csrf"
        members.return_value.get_administered_organizations.return_value = [
            {"id": 12, "name": "Caf&eacute;", "type": "licence"},
            {"id": "13", "name": "New", "type": "licence"},
        ]
        sync_administered_organizations(user.id)

    kept.refresh_from_db()
    assert kept.name == "Café"
    assert kept.portal_id == "12"
    assert kept.last_sync is None
    assert set(kept.admins.all()) == {user}
    created = Organization.objects.get(source_id="13")
    assert created.portal_id == "13"
    assert created.name == "New"
    assert user in created.admins.all()
    gone.refresh_from_db()
    assert user not in gone.admins.all()
    assert other in gone.admins.all()
    token_manager.return_value.ensure_offline_token.assert_called_once()


def test_sync_administered_organizations_keeps_admins_when_the_portal_fails(settings):
    settings.RESANA_WEB_ENDPOINT = "https://resana.example"
    user = UserFactory()
    organization = OrganizationFactory(source_id="12")
    organization.admins.add(user)
    with patch("core.sources.resana.organization_sync.ResanaTokenManager"), patch(
        "core.sources.resana.organization_sync.ResanaMembersClient"
    ) as members:
        members.return_value.get_administered_organizations.side_effect = (
            ResanaPortalError("refused")
        )
        with pytest.raises(ResanaPortalError):
            sync_administered_organizations(user.id)
    assert user in organization.admins.all()


class _GedSource(AbstractSourceBackend):
    """One shared workspace whose organization id the test sets."""

    source_type = "fake"
    label = "Fake"
    organization_id = "12"

    def get_workspaces(self, user):
        return [
            SourceWorkspace(
                id="ws-org",
                title="With org",
                organization_id=self.organization_id,
                organization_name="Mairie",
            )
        ]

    def get_workspace_structure(self, workspace):
        return SourceFolder(name="root")

    def download_file(self, file, destination_path):
        pass


GED_SOURCE_PATH = f"{_GedSource.__module__}._GedSource"


def test_ged_reuses_the_portal_organization_when_the_ids_match(settings):
    """The same string on the portal and the GED stays one organization."""
    settings.SOURCE_BACKEND = GED_SOURCE_PATH
    _GedSource.organization_id = "12"
    portal = OrganizationFactory(source_id="12", name="Old")
    SourceManager().synchronize(UserFactory())

    assert Organization.objects.count() == 1
    portal.refresh_from_db()
    assert portal.name == "Mairie"
    assert Workspace.objects.get(source_id="ws-org").organization == portal


def test_ged_creates_a_row_when_its_id_differs(settings):
    """A GED id that is not the portal id stays its own organization."""
    settings.SOURCE_BACKEND = GED_SOURCE_PATH
    _GedSource.organization_id = "03-01-abc"
    OrganizationFactory(source_id="12", portal_id="12", name="Mairie")
    SourceManager().synchronize(UserFactory())

    assert Organization.objects.count() == 2
    created = Organization.objects.get(source_id="03-01-abc")
    assert created.portal_id is None
    assert created.name == "Mairie"
