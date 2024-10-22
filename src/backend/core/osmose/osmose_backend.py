import os
import re
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
        self.name = self.name.replace("/", "-")
        self.children = []
        self.files: [OsmoseFile] = []


class OsmoseFile:
    def __init__(self, raw_data=None):
        self.raw_data = raw_data
        self.name = raw_data["title"] if raw_data and "title" in raw_data else "None"

    @property
    def name_with_extension(self):
        return self.name + self.extension

    @property
    def extension(self):
        if "originalFilename" in self.raw_data:
            return os.path.splitext(self.raw_data["originalFilename"])[1]
        if "downloadUrl" in self.raw_data:
            match = re.findall(r"\.(\w+)$", self.raw_data["downloadUrl"])
            if match:
                return "." + match[0]
        return ""


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
        osmose_workspaces = backend.get_workspaces(user)
        for osmoseWorkspace in osmose_workspaces:
            workspace = Workspace.objects.filter(osmose_id=osmoseWorkspace.id).first()
            if not workspace:
                workspace = Workspace()
                workspace.osmose_id = osmoseWorkspace.id
                workspace.title = osmoseWorkspace.title
            workspace.save()
            user.workspaces.add(workspace)
