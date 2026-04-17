"""Synchronize API view."""
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import IsAuthenticated
from core.backends.source import SourceManager
from core.models import FeatureFlag
from core.utils import is_feature


class SynchronizeAPIView(APIView):
    """Synchronize API view."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Synchronize the user's workspaces with the configured source backend."""
        if is_feature(FeatureFlag.Name.READ_ONLY_MODE):
            return Response({"message": "Read only mode is enabled."})
        manager = SourceManager()
        manager.synchronize(request.user)
        return Response()
