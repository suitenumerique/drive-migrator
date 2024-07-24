"""Synchronize API view."""
from rest_framework.response import Response
from rest_framework.views import APIView

from core.osmose.osmose_backend import OsmoseManager


class SynchronizeAPIView(APIView):
    """Synchronize API view."""

    def get(self, request):
        """Synchronize the user's workspaces with Osmose."""
        manager = OsmoseManager()
        manager.synchronize(request.user)
        return Response()
