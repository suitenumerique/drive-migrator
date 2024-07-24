import urllib.request
from abc import ABC, abstractmethod
from enum import Enum

from django.conf import settings
from django.utils.module_loading import import_string

from core.models import User, Workspace


class WorkspaceStatusEnum(Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    FAILURE = "FAILURE"
    SUCCESS = "SUCCESS"


class OsmoseWorkspace:
    def __init__(self, id, title="", raw_data=None):
        self.id = id
        self.title = title
        self.raw_data = raw_data


class OsmoseFolder:
    def __init__(self, raw_data=None):
        self.raw_data = raw_data
        self.name = raw_data["name"] if raw_data and "name" in raw_data else "None"
        self.children = []
        self.files: [OsmoseFile] = []


class OsmoseFile:
    def __init__(self, raw_data=None):
        self.raw_data = raw_data
        self.name = raw_data["title"] if raw_data and "title" in raw_data else "None"


class OsmoseBackend(ABC):
    @abstractmethod
    def get_workspaces(self, user):
        pass

    def download_file(self, download_url, destination):
        urllib.request.urlretrieve(download_url, destination)  # noqa: S310


class OsmoseManager:
    def get_backend(self):
        backend_class = import_string(settings.OSMOSE_BACKEND)
        backend = backend_class()
        return backend

    def synchronize(self, user: User):
        backend = self.get_backend()
        osmoseWorkspaces = backend.get_workspaces(user)
        for osmoseWorkspace in osmoseWorkspaces:
            workspace = Workspace.objects.filter(osmose_id=osmoseWorkspace.id).first()
            print("Workspace", osmoseWorkspace, workspace)  # noqa: T201
            if not workspace:
                workspace = Workspace()
                workspace.status = Workspace.Status.NONE
                workspace.osmose_id = osmoseWorkspace.id
                workspace.title = osmoseWorkspace.title
            workspace.save()
            user.workspaces.add(workspace)
