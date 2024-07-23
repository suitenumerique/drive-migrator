from abc import ABC, abstractmethod
from enum import Enum

from django.utils.module_loading import import_string
from django.conf import settings

from core.models import Workspace, User
import urllib.request

class WorkspaceStatusEnum(Enum):
    NONE = 'NONE'
    PENDING = 'PENDING'
    FAILURE = 'FAILURE'
    SUCCESS = 'SUCCESS'

class OsmoseWorkspace:
    def __init__(self, id, title="", raw_data=None):
        self.id = id
        self.title = title
        self.raw_data = raw_data

class OsmoseFolder:
    def __init__(self, name="", raw_data=None):
        self.raw_data = raw_data
        self.name = raw_data["name"] if raw_data and "name" in raw_data else "None"
        self.children = []
        self.files: [OsmoseFile] = []

class OsmoseFile:
    def __init__(self, raw_data = None):
        self.raw_data = raw_data
        self.name = raw_data["title"] if raw_data and "title" in raw_data else "None"



class OsmoseBackend(ABC):

    @abstractmethod
    def get_workspaces(self, user):
        pass

    def download_file(self, download_url, destination):
        urllib.request.urlretrieve(download_url, destination)


class OsmoseManager():

    def get_backend(self):
        backendClass = import_string(settings.OSMOSE_BACKEND)
        backend = backendClass()
        return backend

    def synchronize(self, user: User):
        backend = self.get_backend()
        osmoseWorkspaces = backend.get_workspaces(user)
        for osmoseWorkspace in osmoseWorkspaces:
            workspace = Workspace.objects.filter(osmose_id=osmoseWorkspace.id).first()
            print("Workspace", osmoseWorkspace, workspace)
            if not workspace:
                workspace = Workspace()
                workspace.status = Workspace.Status.NONE
                workspace.osmose_id = osmoseWorkspace.id
                workspace.title = osmoseWorkspace.title
            workspace.save()
            user.workspaces.add(workspace)

