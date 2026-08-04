import json
import logging
import os
import time
import urllib.request
from urllib.error import HTTPError, URLError

from django.conf import settings

import jwt
import requests
from celery.utils.log import get_task_logger
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from tenacity import before_sleep_log, retry, wait_exponential

from core.models import Workspace
from core.retry_utils import log_final_failure_and_reraise
from core.sources.osmose.osmose_backend import (
    OsmoseBackend,
    OsmoseFile,
    OsmoseFolder,
    OsmoseWorkspace,
)
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


def _stop_after_configured_attempts(retry_state) -> bool:
    """Read OSMOSE_RETRY_MAX_ATTEMPTS at call time, not decoration time, so it
    stays overridable per-test/per-environment like every other setting here."""
    return retry_state.attempt_number >= settings.OSMOSE_RETRY_MAX_ATTEMPTS


def _wait_configured_backoff(retry_state) -> float:
    """Same rationale as _stop_after_configured_attempts: read settings live."""
    return wait_exponential(
        multiplier=settings.OSMOSE_RETRY_WAIT_MULTIPLIER,
        min=settings.OSMOSE_RETRY_WAIT_MIN,
    )(retry_state)


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

        raw = {"sub": user, "iss": settings.OSMOSE_JWT_ISS, "exp": expiration}
        if settings.OSMOSE_JWT_IP_MASK:
            raw["ipMask"] = settings.OSMOSE_JWT_IP_MASK

        encoded = jwt.encode(
            raw,
            private_key,
            algorithm="RS256",
        )
        print("JWT")  # noqa: T201
        print(raw)  # noqa: T201
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

    @retry(
        stop=_stop_after_configured_attempts,
        wait=_wait_configured_backoff,
        before_sleep=before_sleep_log(get_logger(), logging.INFO),
        retry_error_callback=log_final_failure_and_reraise(get_logger()),
    )
    def download_file(self, download_url, destination):
        get_logger().info("Downloading %s to %s ...", download_url, destination)
        encoded_url = requests.utils.requote_uri(download_url)
        if download_url != encoded_url:
            download_url = encoded_url
            get_logger().info("Special chars detected, new url: %s", download_url)

        self.init_jwt()
        opener = self.__build_opener()
        urllib.request.install_opener(opener)

        error_ignored = False

        try:
            urllib.request.urlretrieve(download_url, destination)  # noqa: S310

        except HTTPError as e:
            get_logger().error(
                "HTTP Error: %s while downloading %s: %s",
                e.code,
                download_url,
                e.reason,
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
                "URL Error: Failed to reach %s. Reason: %s", download_url, e.reason
            )
            raise e
        except OSError as e:
            get_logger().error("OS Error: %s while writing to %s", e, destination)
            raise e
        except Exception as e:
            get_logger().error("Unexpected error occurred: %s", e)
            raise e

        get_logger().info("Success %s to %s ...", download_url, destination)
        if error_ignored:
            get_logger().info("Error ignored.")
        else:
            size = os.stat(destination).st_size
            size_formatted = sizeof_fmt(size)
            get_logger().info("File: %s %s (%s) ...", destination, size_formatted, size)

    def get_members(self, workspace) -> list[dict]:
        """Return workspace members as a list of {name, firstName, email} dicts."""
        users = self.__fetch_users(workspace)
        get_logger().info("Members fetched: %s", len(users))
        return [
            {
                "name": user.get("name", ""),
                "firstName": user.get("firstName", ""),
                "email": user.get("email", ""),
            }
            for user in users
            if user
        ]

    def fetch(self, url, params=None):
        self.init_jwt()
        response = requests.get(
            settings.OSMOSE_API_ENDPOINT + url,
            params=params,
            headers={
                "Authorization": "Bearer " + self.jwt,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30,
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
        with open(f"{path}/{filename}.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=4))

    def get_workspaces(self, user):
        osmose_user = self.__get_user(user.email)
        print("get_workspaces")  # noqa: T201
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
        print("data", data)  # noqa: T201
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
                params={"wrkspc": workspace.source_id, **params},
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
        osmose_workspace = self.get_workspace(workspace.source_id)
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

        get_logger().info("Root categories: %s", len(root_categories))
        get_logger().info("Categories: %s", len(categories))

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
                "email": email.lower(),
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
