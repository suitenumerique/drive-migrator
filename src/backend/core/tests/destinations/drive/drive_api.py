"""A scripted La Suite Drive API, served through the responses library."""

import json
import re
from urllib.parse import parse_qs, urlparse

import responses
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

_TOKEN = "https://oidc.example.com/token"
_API = "https://drive.example.com/external_api/v1.0"
_S3 = "https://s3.example.com/upload"


def _validation(code):
    body = {
        "type": "validation_error",
        "errors": [{"code": code, "detail": "Refused.", "attr": None}],
    }
    return 400, {"Content-Type": "application/json"}, json.dumps(body)


def _html_block():
    return 403, {"Content-Type": "text/html"}, "<html>Request unsuccessful.</html>"


class DriveApi:  # pylint: disable=too-many-instance-attributes
    """Records every Drive call and answers it.

    Files created by an export are remembered, so a later integrity check reads
    them back. Tests adjust that memory (title, size, upload state) or make a
    named file fail before calling the code.
    """

    def __init__(self):
        self.users = {}
        self.reject_create = {}
        self.reject_ended = {}
        self.block_create = set()
        self.fatal_create = set()
        self.fail_root = False
        self.fail_listing = None
        self.probe = "ok"
        self.block_ended = False
        self.me_status = 200
        self.me = {"id": "alice-uuid"}
        self.promote_on_get = False
        self.items = {}
        self.extra = {}
        self._rsps = None

    def __enter__(self):
        self._rsps = responses.RequestsMock(assert_all_requests_are_fired=False)
        self._rsps.__enter__()
        for method, handler in (
            (responses.POST, self._post),
            (responses.GET, self._get),
            (responses.PUT, self._put),
            (responses.DELETE, self._delete),
        ):
            self._rsps.add_callback(method, re.compile(r"https://"), callback=handler)
        return self

    def __exit__(self, *exc):
        return self._rsps.__exit__(*exc)

    @property
    def calls(self):
        return self._rsps.calls

    def item_by_filename(self, filename):
        return next(
            item for item in self.items.values() if item.get("filename") == filename
        )

    def set_state(self, filename, upload_state):
        self.item_by_filename(filename)["upload_state"] = upload_state

    def resize(self, filename, size):
        self.item_by_filename(filename)["size"] = size

    def retitle(self, filename, title):
        self.item_by_filename(filename)["title"] = title

    def drop(self, filename):
        item = self.item_by_filename(filename)
        del self.items[item["id"]]

    def _post(self, request):
        url = request.url
        if url.startswith(_TOKEN):
            return _ok({"access_token": "service-tok", "expires_in": 3600})
        if url.endswith("/upload-ended/"):
            return self._upload_ended(url)
        if url.endswith("/accesses/") or url.endswith("/invitations/"):
            return _ok({})
        if "/children/" in url:
            return self._create_child(request)
        if url.rstrip("/").endswith("/items"):
            return self._create_root(request)
        return 404, {}, ""

    def _get(self, request):
        url = request.url.split("?", 1)[0]
        if url.endswith("/users/me/"):
            if self.me_status != 200:
                return self.me_status, {}, ""
            return _ok(self.me)
        if url.endswith("/users/"):
            query = parse_qs(urlparse(request.url).query).get("q", [""])[0]
            user_id = self.users.get(query)
            results = [{"id": user_id, "email": query}] if user_id else []
            return _ok({"results": results})
        if url.endswith("/children/"):
            if self.fail_listing:
                raise RuntimeError(self.fail_listing)
            parent = url.rstrip("/").split("/")[-2]
            return _ok({"results": self._children(parent)})
        if "/items/" in url:
            return self._read_item(url.rstrip("/").split("/")[-1])
        return 404, {}, ""

    def _put(self, request):
        if request.url.startswith(_S3):
            return 200, {}, ""
        return 404, {}, ""

    def _delete(self, _request):
        return 204, {}, ""

    def _create_root(self, request):
        if self.fail_root:
            return 500, {}, "down"
        return self._store(request, parent=None)

    def _create_child(self, request):
        parent = request.url.rstrip("/").split("/")[-2]
        body = json.loads(request.body)
        filename = body.get("filename")
        if filename in self.fatal_create:
            return 500, {}, "down"
        if filename in self.block_create:
            return _html_block()
        if filename in self.reject_create:
            return _validation(self.reject_create[filename])
        return self._store(request, parent=parent)

    def _store(self, request, parent):
        body = json.loads(request.body)
        item_id = body["id"]
        is_file = body.get("type") == "file"
        item = {
            "id": item_id,
            "type": body.get("type"),
            "title": body.get("title") or body.get("filename"),
            "parent": parent,
        }
        if is_file:
            item["filename"] = body["filename"]
            item["size"] = body.get("size")
            item["upload_state"] = "ready"
            item["policy"] = f"{_S3}/{item_id}"
        self.items[item_id] = item
        shown = {key: value for key, value in item.items() if key != "parent"}
        return _ok(shown)

    def _upload_ended(self, url):
        if self.block_ended:
            return _html_block()
        item_id = url.rstrip("/").split("/")[-2]
        item = self.items.get(item_id, {})
        code = self.reject_ended.get(item.get("filename"))
        if code:
            return _validation(code)
        return _ok({})

    def _read_item(self, item_id):
        if self.probe == "blocked":
            return _html_block()
        if self.probe == "missing":
            return 404, {}, ""
        if self.probe == "timeout":
            raise Timeout("timed out")
        if self.probe == "connection":
            raise RequestsConnectionError("connection failed")
        item = self.items.get(item_id)
        if item is None:
            return 404, {}, ""
        if self.promote_on_get and item.get("upload_state") == "analyzing":
            item = {**item, "upload_state": "ready"}
            self.items[item_id] = item
        return _ok({key: value for key, value in item.items() if key != "parent"})

    def _children(self, parent):
        listed = [
            {
                key: value
                for key, value in item.items()
                if key not in ("parent", "policy")
            }
            for item in self.items.values()
            if item.get("parent") == parent and item.get("upload_state") != "pending"
        ]
        listed.extend(self.extra.get(parent, []))
        return listed


def _ok(payload):
    return 200, {"Content-Type": "application/json"}, json.dumps(payload)
