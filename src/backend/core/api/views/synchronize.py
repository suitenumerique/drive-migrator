"""Synchronize API view."""
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import FeatureFlag
from core.osmose.osmose_backend import OsmoseManager
from core.utils import is_feature


class SynchronizeAPIView(APIView):
    """Synchronize API view."""

    def get(self, request):
        """Synchronize the user's workspaces with Osmose."""
        if is_feature(FeatureFlag.Name.READ_ONLY_MODE):
            return Response({"message": "Read only mode is enabled."})
        manager = OsmoseManager()
        manager.synchronize(request.user)
        return Response()
