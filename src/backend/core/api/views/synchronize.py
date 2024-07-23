from rest_framework.views import APIView

from core.osmose.osmose_backend import OsmoseManager
from rest_framework.response import Response
from .. import APIException

class SynchronizeAPIView(APIView):

    def get(self, request):
        manager = OsmoseManager()
        manager.synchronize(request.user)
        return Response()
