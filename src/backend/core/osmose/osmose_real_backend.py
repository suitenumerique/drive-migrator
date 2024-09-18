import json
import os
import time
import urllib.request

from django.conf import settings

import jwt
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from core.models import Workspace
from core.osmose.osmose_backend import (
    OsmoseBackend,
    OsmoseFile,
    OsmoseFolder,
    OsmoseWorkspace,
)


class OsmoseRealBackend(OsmoseBackend):
    def __init__(self):
        self.jwt = None

    def create_jwt(self, user):  # pylint: disable=unused-argument
        private_key = serialization.load_pem_private_key(
            bytes(settings.OSMOSE_PKI_RSA_PRIVATE_KEY, "utf-8"),
            password=bytes(settings.OSMOSE_PKI_RSA_PRIVATE_KEY_PASSPHRASE, "utf-8"),
            backend=default_backend(),
        )

        expiration = int(time.time()) + 120

        encoded = jwt.encode(
            {"sub": "admin", "iss": settings.OSMOSE_JWT_ISS, "exp": expiration},
            private_key,
            algorithm="RS256",
        )
        return encoded

    def init_jwt(self):
        if not self.jwt:
            self.jwt = self.create_jwt(settings.OSMOSE_JWT_SUB)

    def download_file(self, download_url, destination):
        self.init_jwt()
        opener = urllib.request.build_opener()
        opener.addheaders = [("Authorization", "Bearer " + self.jwt)]
        urllib.request.install_opener(opener)
        urllib.request.urlretrieve(download_url, destination)  # noqa: S310

    def fetch(self, url, params=None):
        self.init_jwt()
        response = requests.get(  # noqa: S113
            settings.OSMOSE_API_ENDPOINT + url,
            params=params,
            headers={
                "Authorization": "Bearer " + self.jwt,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()
        return data

    def get_workspace(self, workspace_id):
        workspace = self.fetch("/data/" + workspace_id)
        return OsmoseWorkspace(
            id=workspace["id"], title=workspace["title"], raw_data=workspace
        )

    def debug_into_file(self, filename, data):
        if not settings.OSMOSE_BACKEND_DEBUG:
            return
        path = "debug"
        if not os.path.exists(path):
            os.mkdir(path)
        with open(f"{path}/{filename}.json", "w") as f:
            f.write(json.dumps(data, indent=4))

    def get_workspaces(self, user):
        osmose_user = self.__get_user(user.email)
        print("osmose_user", osmose_user)
        if not osmose_user:
            raise Exception(f"User {user.email} not found in Osmose")

        # "start" parameter could be used for pagination.
        # belongsToWorkspace=true&member=${user.id}
        data = self.fetch(
            "/search/workspace",
            params={
                "pageSize": "1000",
                "belongsToWorkspace": True,
                "member": osmose_user["id"],
            },
        )

        self.debug_into_file("workspaces", data)

        workspaces = []
        for workspace in data["dataSet"]:
            if workspace["model"]:
                continue
            if not any(
                admin["id"] == osmose_user["id"]
                for admin in workspace["administrators"]
            ):
                continue
            workspaces.append(
                OsmoseWorkspace(
                    id=workspace["id"], title=workspace["title"], raw_data=workspace
                )
            )
        return workspaces

    def get_workspace_documents_structure(self, workspace: Workspace):
        """
        Fetch the workspace documents structure with descendants.

        :param workspace: a OsmoseWorkspace
        :return: a OsmoseFolder representing the workspace documents structure with descendants.
        """
        osmose_workspace = self.get_workspace(workspace.osmose_id)
        root_categories = []
        categories = []
        for root_category in osmose_workspace.raw_data["catSet"]:
            # Fetch root category data
            root_data = self.fetch("/data/" + root_category["id"])
            self.debug_into_file("root_data", root_data)
            root_categories.append(root_data)

            # Fetch children of root category
            data = self.fetch(
                "/search/category", params={"rootCid": root_category["id"]}
            )
            self.debug_into_file("cat", data)

            for cat in data["dataSet"]:
                categories.append(cat)

        builder = FolderBuilder()
        folder = builder.build(root_categories, categories)
        self.__fetch_files_in_folders(folder)
        return folder

    def __fetch_files_in_folders(self, folder: OsmoseFolder):
        if folder.raw_data:
            self.get_folder_files(folder)
        for child in folder.children:
            self.__fetch_files_in_folders(child)

    # TODO: Handle pagination ? # pylint: disable=fixme
    def get_folder_files(self, folder: OsmoseFolder):
        data = self.fetch(
            "/search",
            params={
                "documentKinds": "filedocument",
                "cids": folder.raw_data["id"],
                "exactCat": True,
                "pageSize": "1000",
            },
        )
        self.debug_into_file(f"files-{folder.raw_data['id']}", data)
        for file_raw in data["dataSet"]:
            file = OsmoseFile(raw_data=file_raw)
            folder.files.append(file)

    def __get_user(self, email):
        data = self.fetch(
            "/search/member",
            params={
                "email": email,
            },
        )
        self.debug_into_file("user", data)

        if len(data["dataSet"]) == 0:
            return None

        return data["dataSet"][0]


class FolderBuilder:
    def build(self, root_categories, categories):
        """
        Build a OsmoseFolder hierarchy from root categories and categories.

        :param root_categories:
        :param categories:
        :return: a OsmoseFolder representing the hierarchy with descendants.
        """
        root = OsmoseFolder()
        for root_category in root_categories:
            folder = self.__build_folder(root_category, categories)
            root.children.append(folder)
        return root

    def __build_folder(self, category, categories):
        folder = OsmoseFolder(raw_data=category)
        raw_children = self.__get_children(category, categories)
        for raw_child in raw_children:
            folder.children.append(self.__build_folder(raw_child, categories))
        return folder

    def __get_children(self, category, categories):
        children = []
        for cat in categories:
            if cat["parent"]["id"] == category["id"]:
                children.append(cat)
        return children
