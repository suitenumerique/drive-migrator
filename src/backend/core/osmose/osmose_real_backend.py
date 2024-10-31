import csv
import json
import logging
import os
import re
import time
import urllib.request
from urllib.error import HTTPError, URLError

from django.conf import settings

import jwt
import requests
from celery.utils.log import get_task_logger
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from retry import retry

from core.models import Workspace
from core.osmose.osmose_backend import (
    OsmoseBackend,
    OsmoseFile,
    OsmoseFolder,
    OsmoseWorkspace,
)
from core.processing.folder_creator import FolderCreator
from core.utils import sizeof_fmt


def get_logger():
    logging.basicConfig()
    logger = get_task_logger(__name__)
    logger.setLevel(logging.DEBUG)
    return logger


PAGE_SIZE = 100
MAX = 500


class PageWalker:
    def __init__(self, callback, **opts):
        self.callback = callback
        self.page_size = PAGE_SIZE
        if "pageSize" in opts:
            self.page_size = opts["pageSize"]

        self.max = MAX
        self.total = None
        self.start = 0
        self.count = 0

    def walk(self):
        output = []

        total = None
        start = 0
        count = 0

        request = True
        while request:
            count += 1
            # get_logger().info(f"Fetching index {start}...")
            response = self.callback(pageSize=self.page_size, start=start)
            if total is None:
                total = response["total"]
                # get_logger().info(f"Total: {total}")

            # get_logger().info(
            #     f"Got response: total: {response['total']}, start: {response['start']}, sort: {response['sort']}"
            # )

            data = response["dataSet"]
            # get_logger().info(f"Data len: {len(data)}")
            output.extend(data)

            request = len(output) < total
            start += len(data)
            if count >= MAX:
                get_logger().warning("Max count reached")
                break

        return output


class OsmoseFailedDownloadException(Exception):
    pass


class OsmoseRealBackend(OsmoseBackend):
    def __init__(self):
        self.jwt = None
        self.cookies = {}

    def create_jwt(self, user):  # pylint: disable=unused-argument
        private_key = serialization.load_pem_private_key(
            bytes(settings.OSMOSE_PKI_RSA_PRIVATE_KEY, "utf-8"),
            password=bytes(settings.OSMOSE_PKI_RSA_PRIVATE_KEY_PASSPHRASE, "utf-8"),
            backend=default_backend(),
        )

        expiration = int(time.time()) + 60 * 60 * 24

        encoded = jwt.encode(
            {"sub": "admin", "iss": settings.OSMOSE_JWT_ISS, "exp": expiration},
            private_key,
            algorithm="RS256",
        )
        return encoded

    def init_jwt(self):
        if not self.jwt:
            self.jwt = self.create_jwt(settings.OSMOSE_JWT_SUB)

    def __build_opener(self):
        opener = urllib.request.build_opener()
        cookies = "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
        opener.addheaders = [
            ("Authorization", "Bearer " + self.jwt),
            ("Cookie", cookies),
        ]
        return opener

    @retry(tries=5, delay=2, backoff=2)
    def download_file(self, download_url, destination):
        get_logger().info(f"Downloading {download_url} to {destination} ...")
        self.init_jwt()
        opener = self.__build_opener()
        urllib.request.install_opener(opener)

        error_ignored = False

        try:
            local_filename, headers = urllib.request.urlretrieve(  # noqa: S310
                download_url, destination
            )
            # This is no longer needed as Baleen has been disabled
            # plus, it was slowing down the download process.
            # self.__handle_validation(headers, destination)

        except HTTPError as e:
            get_logger().error(
                f"HTTP Error: {e.code} while downloading {download_url}: {e.reason}"  # . Response body: {e.read().decode()
            )

            # response = requests.get(
            #     download_url,
            #     headers={
            #         "Authorization": "Bearer " + self.jwt,
            #     },
            # )
            # get_logger().error(
            #     f"HTTP Error: Additional request response: {response.text}"
            # )

            if e.code == 404 and settings.OSMOSE_BACKEND_ACCEPT_404:
                error_ignored = True
            else:
                raise e
        except URLError as e:
            get_logger().error(
                f"URL Error: Failed to reach {download_url}. Reason: {e.reason}"
            )
            raise e
        except OSError as e:
            get_logger().error(f"OS Error: {e} while writing to {destination}")
            raise e
        except Exception as e:
            get_logger().error(f"Unexpected error occurred: {e}")
            raise e

        get_logger().info(f"Success {download_url} to {destination} ...")
        if error_ignored:
            get_logger().info("Error ignored.")
        else:
            size = os.stat(destination).st_size
            size_formatted = sizeof_fmt(size)
            get_logger().info(f"File: {destination} {size_formatted} ({size}) ...")

    def create_users_csv(self, workspace):
        users = self.__fetch_users(workspace)
        get_logger().info(f"Users to write: {len(users)}")
        folder_creator = FolderCreator()
        path = os.path.join(
            folder_creator.get_workspace_path(workspace), "osmose_users.csv"
        )
        with open(path, "w") as file:
            writer = csv.writer(file)
            row_list = []
            for user in users:
                row_list.append([user["name"], user["firstName"], user["email"]])
            writer.writerows(row_list)

    def __handle_validation(self, headers, destination):
        if headers.get("Content-Type") != "text/html":
            return

        with open(destination) as f:
            content = f.read(10000)
            if "__blnChallengeStore" in content:
                get_logger().info("Challenge detected, solving...")

                # Retrieve the cookie in the page.
                raw_data = re.findall(r"(?<=__blnChallengeStore=)\{[^\;]+", content)
                assert raw_data and len(raw_data) == 1  # noqa: S101
                raw_data = "".join(raw_data)
                raw_data = json.loads(raw_data)

                challenge_cookie = raw_data["cookie"]
                # Add it to the cookies for the next retry request.
                self.cookies[challenge_cookie["name"]] = challenge_cookie["value"]

                # Send the check request.
                check_params = raw_data["checkChallengeParams"]
                data = "&".join(["%s=%s" % (k, v) for k, v in check_params.items()])

                url = (
                    settings.OSMOSE_BASE_ENDPOINT
                    + "/.well-known/baleen/challengejs/check?%s=%s"
                    % (challenge_cookie["name"], challenge_cookie["value"])
                )
                get_logger().info(f"Sending check request to {url} with data {data}")
                response = requests.post(  # noqa: S113
                    url,
                    data,
                    headers={
                        "Authorization": "Bearer " + self.jwt,
                    },
                )
                response.raise_for_status()

                get_logger().info("Success challenge, replaying download...")
                raise OsmoseFailedDownloadException(
                    "Challenge solved, retrying download..."
                )

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
        self.debug_into_file("data_workspace_" + workspace_id, workspace)
        return OsmoseWorkspace(
            id=workspace["id"], title=workspace["title"], raw_data=workspace
        )

    def debug_into_file(self, filename, data):
        if not settings.OSMOSE_BACKEND_DEBUG:
            return
        path = "/tmp"  # noqa: S108
        if not os.path.exists(path):
            os.mkdir(path)
        with open(f"{path}/{filename}.json", "w") as f:
            f.write(json.dumps(data, indent=4))

    def get_workspaces(self, user):
        osmose_user = self.__get_user(user.email)
        print("osmose_user", osmose_user)  # noqa: T201
        if not osmose_user:
            return []

        # "start" parameter could be used for pagination.
        # belongsToWorkspace=true&member=${user.id}
        data = self.fetch(
            "/search/workspace",
            params={
                "pageSize": "1000",  # Based on stats, there is no users with more than 1000 workspaces
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

    def __fetch_users(self, workspace):
        def fetch(**params):
            response = self.fetch(
                "/search/member",
                params={"wrkspc": workspace.osmose_id, **params},
            )
            self.debug_into_file(f"users_{workspace.id}", response)
            return response

        walker = PageWalker(callback=fetch, pageSize=150)
        return walker.walk()

    def __fetch_categories_by_root(self, root_category):
        def fetch(**params):
            response = self.fetch(
                "/search/category",
                params={"rootCid": root_category["id"], **params},
            )
            self.debug_into_file(
                f"cat_{root_category['id']}_{params['start']}", response
            )
            return response

        walker = PageWalker(callback=fetch)
        return walker.walk()

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
            cats = self.__fetch_categories_by_root(root_category)
            categories.extend(cats)

        get_logger().info(f"Root categories: {len(root_categories)}")
        get_logger().info(f"Categories: {len(categories)}")

        builder = FolderBuilder()
        folder = builder.build(root_categories, categories)
        self.__fetch_files_in_folders(folder)
        return folder

    def __fetch_files_in_folders(self, folder: OsmoseFolder):
        if folder.raw_data:
            self.get_folder_files(folder)
        for child in folder.children:
            self.__fetch_files_in_folders(child)

    def get_folder_files(self, folder: OsmoseFolder):
        def fetch(**params):
            response = self.fetch(
                "/search",
                params={
                    "documentKinds": "filedocument",
                    "cids": folder.raw_data["id"],
                    "exactCat": True,
                    **params,
                },
            )
            self.debug_into_file(f"files-{folder.raw_data['id']}", response)
            return response

        walker = PageWalker(callback=fetch)
        raw_files = walker.walk()
        for file_raw in raw_files:
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
