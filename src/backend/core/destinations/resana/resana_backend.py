import json
import logging

from django.conf import settings

import requests
from celery.utils.log import get_task_logger

from core.mails_manager import MailsManager
from core.models import ResanaEmailMapping, User, Workspace
from core.processing.folder_creator import FolderCreator
from core.destinations.resana.s3_resana_manager import S3ResanaManager
from core.utils import truncate_folder_and_file_names


def get_logger():
    logging.basicConfig()
    logger = get_task_logger(__name__)
    logger.setLevel(logging.DEBUG)
    return logger


class ResanaBackend:
    def __init__(self):
        self.session = None
        self.jwt = None

    def get_mapping_from_email(self, email):
        # extract domain from email
        parts = email.split("@")
        if len(parts) != 2:
            raise Exception("Invalid email")
        domain = parts[1]
        mapping = ResanaEmailMapping.objects.filter(domain=domain).first()
        if mapping:
            return mapping
        mapping = ResanaEmailMapping.objects.filter(domain="*").first()
        if not mapping:
            raise Exception("No default mapping found")
        return mapping

    def get_destination_organization_uuid(self, user: User, workspace: Workspace):
        if settings.RESANA_DEFAULT_ORGANIZATION:
            return settings.RESANA_DEFAULT_ORGANIZATION
        mapping = self.get_mapping_from_email(user.email)
        return mapping.resana_organization_uuid

    def get_error_details(self, workspace: Workspace):
        job_id = workspace.get_destination_metadata("resana").get("job_id")
        if not job_id:
            raise ValueError("Workspace must have a resana job id")
        response = self.request(
            "get",
            f"/jobs/{job_id}/tasks",
            params={"itemsPerPage": 1000, "status": 4, "type": 2},
        )
        return response.json()

    def retry_job(self, workspace: Workspace):
        job_id = workspace.get_destination_metadata("resana").get("job_id")
        if not job_id:
            raise ValueError("Workspace must have a resana job id")
        response = self.request("post", f"/jobs/{job_id}/retry", json={})

        workspace.set_destination_status("resana", Workspace.Status.PENDING)
        workspace.save()

        return response.json()

    def fetch_user(self, user: User):
        get_logger().info(f"Search Resana user {user.email} ...")
        response = self.request(
            "get",
            "/contacts/users",
            params={"search": user.email},
            base_url=settings.RESANA_ALT_API_ENDPOINT,
        )

        data = response.json()
        users = data["users"]
        if len(users) == 0:
            get_logger().info(f"User not found: {user.email}")
            return None

        user = users[0]
        get_logger().info(f"User found: {user}")
        return user

    def add_admin(self, workspace: Workspace, user):
        resana_id = workspace.get_destination_metadata("resana").get("id")
        if not resana_id:
            raise Exception("Workspace not created in Resana")

        get_logger().info(f"Adding admin to {resana_id} {workspace.title} ...")
        response = self.request(
            "post",
            "/api-workspaces/members",
            json={
                "userUuid": user["uuid"],
                "workspaceUuid": resana_id,
                "profileCode": "GESTIONNAIRE",
            },
            base_url=settings.RESANA_ALT_API_ENDPOINT,
        )
        get_logger().info(json.dumps(response.json(), indent=2))

    def get_organizations(self):
        # Get organizations.
        get_logger().info("Fetching organizations ...")
        response = self.request("get", f"/organizations", params={"itemsPerPage": 100})
        data = response.json()
        organizations = data["hydra:member"]
        return organizations

    def create_workspace(self, workspace: Workspace, user: User):
        if workspace.get_destination_metadata("resana").get("id"):
            raise Exception("Workspace already created in Resana")

        # Fetch resana user to make sure it exists before proceeding to upload.
        resana_user = self.fetch_user(user)
        if not resana_user:
            # TODO: Add specific logic here.
            raise Exception(f"User {user.email} not found in Resana")

        # Get organizations.
        # get_logger().info("Fetching organizations ...")
        # response = self.request("get", f"/organizations")
        # data = response.json()
        # get_logger().info(json.dumps(data, indent=2))

        # Make sure folder and file name are not too long.
        folder_creator = FolderCreator()
        path = folder_creator.get_workspace_path(workspace)
        truncate_folder_and_file_names(path)

        # Upload folder to S3.
        get_logger().info("Calling upload_folder ...")
        s3_manager = S3ResanaManager()
        upload_path = s3_manager.upload_folder(workspace)

        # Get organization data.
        organization_uuid = self.get_destination_organization_uuid(user, workspace)
        get_logger().info(
            f"Creating workspace inside organization {organization_uuid} ..."
        )
        response = self.request("get", f"/organizations/{organization_uuid}")
        organization_data = response.json()

        # get_logger().info("Organization data")
        # get_logger().info(json.dumps(organization_data, indent=2))

        # Create workspace.
        response = self.request(
            "post",
            f"/workspaces",
            json={
                "name": workspace.title.replace("/", "").replace("\\", ""),
                "organizationUuid": organization_uuid,
                "color": "#ffffff",
            },
        )
        workspace_data = response.json()

        get_logger().info("Workspace creation data")
        get_logger().info(json.dumps(workspace_data, indent=2))

        resana_id = workspace_data["uuid"]
        workspace.set_destination_metadata("resana", {"id": resana_id})
        workspace.save()

        # Add user as admin.
        self.add_admin(workspace, resana_user)

        # Create job.
        response = self.request(
            "post",
            "/jobs",
            json={
                "type": "import",
                "destinationWorkspaceUuid": resana_id,
                "importPath": upload_path,
            },
        )
        get_logger().info("Job creation data")
        get_logger().info(json.dumps(response.json(), indent=2))
        job_data = response.json()

        resana_meta = workspace.get_destination_metadata("resana")
        resana_meta["job_id"] = job_data["uuid"]
        workspace.set_destination_metadata("resana", resana_meta)
        workspace.save()

    def fetch_job(self, workspace: Workspace):
        job_id = workspace.get_destination_metadata("resana").get("job_id")
        if not job_id:
            raise Exception("Workspace has no job id")

        get_logger().info(f"Fetching job of {workspace.id} {workspace.title} ...")
        response = self.request("get", f"/jobs/{job_id}")
        job_data = response.json()
        get_logger().info(json.dumps(job_data, indent=2))

        return job_data

    def refresh_job(self, workspace: Workspace):
        job_id = workspace.get_destination_metadata("resana").get("job_id")
        if not job_id:
            raise Exception("Workspace has no job id")

        if workspace.get_destination_status("resana") == Workspace.Status.SUCCESS:
            raise Exception("Workspace resana destination is already SUCCESS")

        job_data = self.fetch_job(workspace)

        get_logger().info(
            f"Job status of {workspace.id} {workspace.title}: {job_data['status']} ..."
        )

        job_status = job_data["status"]
        resana_meta = workspace.get_destination_metadata("resana")
        resana_meta["files_success"] = job_data["numberOfFilesSuccess"]
        resana_meta["files_error"] = job_data["numberOfFilesError"]
        workspace.set_destination_metadata("resana", resana_meta)

        if job_status in ("completed", "failed"):
            get_logger().info(f"Setting status to success ...")
            workspace.set_destination_status("resana", Workspace.Status.SUCCESS)
            workspace.save()

            if workspace.job_status == "completed":
                get_logger().info("Sending send_resana_ready_mail ...")
                mails_manager = MailsManager()
                mails_manager.send_resana_ready_mail(
                    workspace.migration_user, workspace
                )

            elif workspace.job_status == "failed":
                get_logger().info("Sending send_resana_ready_errors_mail ...")
                mails_manager = MailsManager()
                mails_manager.send_resana_ready_errors_mail(
                    workspace.migration_user, workspace
                )

    def request(self, method, url, **kwargs) -> requests.Response:
        self.init_jwt()

        if method == "get":
            func = self.session.get
        else:
            func = self.session.post

        full_url = settings.RESANA_API_ENDPOINT + url
        if base_url := kwargs.pop("base_url", None):
            full_url = base_url + url

        response = func(full_url, **kwargs)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            get_logger().error(
                f"Error while calling {method} {url} {response.status_code}"
            )
            print(response.text)  # noqa: T201
            get_logger().error(json.dumps(e.response.json(), indent=2))
            raise e

        return response

    def create_jwt(self):
        self.session = requests.Session()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response = self.session.post(
            settings.RESANA_AUTH_ENDPOINT,
            {
                "mail_inscription": settings.RESANA_AUTH_USER,
                "password": settings.RESANA_AUTH_PASSWORD,
                "perimetre_id": "",
                "information_id": "",
                "new_licence": "",
                "choix_formule": "",
                "id_licence": "",
                "parsec_password_derive": "",
                "langue": "",
            },
            headers=headers,
            allow_redirects=False,
        )
        response.raise_for_status()

        self.session.headers[
            "Authorization"
        ] = f'Bearer {self.session.cookies.get("interstis_access")}'
        self.jwt = self.session.cookies.get("interstis_access")

        return self.jwt

    def init_jwt(self):
        if not self.jwt:
            self.jwt = self.create_jwt()
