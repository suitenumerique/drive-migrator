"""Tests for InterstisClient — Interstis GED API HTTP client."""

from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib

from core.sources.resana.interstis_client import InterstisClient

# ---------------------------------------------------------------------------
# Group 1 — Constructor
# ---------------------------------------------------------------------------


def test_client_sets_bearer_token_header(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    with patch("core.sources.resana.interstis_client.requests.Session") as MockSession:
        client = InterstisClient("my-token")

    MockSession.return_value.headers.__setitem__.assert_called_once_with(
        "Authorization", "Bearer my-token"
    )
    assert client.token == "my-token"


def test_client_creates_session_on_init(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    with patch("core.sources.resana.interstis_client.requests.Session") as MockSession:
        InterstisClient("tok")

    MockSession.assert_called_once()


# ---------------------------------------------------------------------------
# Group 2 — get_workspaces()
# ---------------------------------------------------------------------------


def test_get_workspaces_returns_members_from_single_page(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    page_1 = {
        "hydra:member": [
            {"uuid": "ws-1", "name": "Workspace A", "isPersonalWorkspace": False},
            {"uuid": "ws-2", "name": "Workspace B", "isPersonalWorkspace": False},
        ],
        "hydra:totalItems": 2,
    }

    client = InterstisClient("fake-token")
    client.session = MagicMock()
    client.session.get.return_value.json.return_value = page_1
    client.session.get.return_value.raise_for_status = MagicMock()

    result = client.get_workspaces()

    client.session.get.assert_called_once_with(
        "https://resana.example.com/api/api/workspaces",
        params={"page": 1, "itemsPerPage": 500},
        timeout=30,
    )
    assert len(result) == 2
    assert result[0]["uuid"] == "ws-1"


def test_get_workspaces_paginates_until_all_fetched(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    page_1 = {
        "hydra:member": [{"uuid": f"ws-{i}"} for i in range(500)],
        "hydra:totalItems": 750,
    }
    page_2 = {
        "hydra:member": [{"uuid": f"ws-{i}"} for i in range(500, 750)],
        "hydra:totalItems": 750,
    }

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value.raise_for_status = MagicMock()
    client.session.get.return_value.json.side_effect = [page_1, page_2]

    result = client.get_workspaces()

    assert client.session.get.call_count == 2
    assert len(result) == 750


# ---------------------------------------------------------------------------
# Group 3 — explore()
# ---------------------------------------------------------------------------


def test_explore_returns_folders_and_files(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    explore_response = {
        "hydra:member": [
            {
                "uuid": "root-uuid",
                "name": "root",
                "folders": [
                    {
                        "uuid": "folder-1",
                        "name": "Documents",
                        "folders": [],
                        "files": [],
                    },
                ],
                "files": [
                    {"uuid": "file-1", "name": "report", "extension": ".pdf"},
                ],
            }
        ],
        "hydra:totalItems": 1,
    }

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value.json.return_value = explore_response
    client.session.get.return_value.raise_for_status = MagicMock()

    result = client.explore("root-uuid")

    client.session.get.assert_called_once_with(
        "https://resana.example.com/api/api/targets/root-uuid/explore",
        params={"page": 1, "itemsPerPage": 500},
        timeout=30,
    )
    assert len(result) == 1
    assert result[0]["uuid"] == "root-uuid"
    assert result[0]["folders"][0]["uuid"] == "folder-1"
    assert result[0]["files"][0]["uuid"] == "file-1"


# ---------------------------------------------------------------------------
# Group 4 — download_file()
# ---------------------------------------------------------------------------


def test_download_file_writes_binary_content_to_path(settings, tmp_path):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    destination = str(tmp_path / "output.pdf")
    file_content = b"%PDF binary content"

    mock_response = MagicMock()
    mock_response.iter_content.return_value = [file_content]
    mock_response.raise_for_status = MagicMock()
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value = mock_response

    client.download_file("file-uuid-123", destination)

    client.session.get.assert_called_once_with(
        "https://resana.example.com/api/api/targets/file-uuid-123/download",
        stream=True,
        timeout=60,
    )
    with open(destination, "rb") as f:
        assert f.read() == file_content


def test_download_file_raises_on_http_error(settings):
    settings.RESANA_API_ENDPOINT = "https://resana.example.com/api"

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = req_lib.HTTPError("404")
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = MagicMock(return_value=False)

    client = InterstisClient("tok")
    client.session = MagicMock()
    client.session.get.return_value = mock_response

    with pytest.raises(req_lib.HTTPError):
        client.download_file("missing-uuid", "/tmp/x.pdf")
