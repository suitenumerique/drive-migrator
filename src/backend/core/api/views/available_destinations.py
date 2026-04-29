"""Available destinations endpoint."""

from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import IsAuthenticated
from core.backends.destination import DestinationRegistry


class AvailableDestinationsAPIView(APIView):
    """Return the list of destinations configured in DESTINATION_BACKENDS."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        destinations = [
            {"name": dest.name, "label": dest.label}
            for dest in DestinationRegistry.get_all()
        ]
        return Response(destinations)
