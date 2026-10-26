"""Synchronize API view."""
from requests.exceptions import RequestException
from rest_framework.response import Response
from rest_framework.views import APIView

from core.analytics import posthog_capture, workspaces_counts
from core.api import APIException
from core.api.permissions import IsAuthenticated
from core.backends.source import SourceManager
from core.models import FeatureFlag
from core.retrying_session import limited_retries
from core.sources.resana.token_manager import ResanaTokenExpired
from core.utils import is_feature


class SynchronizeAPIView(APIView):
    """Synchronize API view."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Synchronize the user's workspaces with the configured source backend."""
        if is_feature(FeatureFlag.Name.READ_ONLY_MODE):
            return Response({"message": "Read only mode is enabled."})
        manager = SourceManager()
        try:
            # Runs inside the web request: retrying a hanging source would
            # outlast the server's timeout, so each API call is tried once.
            with limited_retries(max_attempts=1):
                manager.synchronize(request.user)
        except ResanaTokenExpired as exc:
            raise APIException("ResanaTokenRequired") from exc
        except RequestException as exc:
            raise APIException("SourceUnavailable") from exc
        posthog_capture(
            "workspaces_synchronized",
            request.user,
            {"$set": workspaces_counts(request.user)},
        )
        return Response()
