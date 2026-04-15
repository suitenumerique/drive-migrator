"""Available destinations endpoint."""

from rest_framework.response import Response
from rest_framework.views import APIView

from core.backends.destination import DestinationRegistry
from core.api.permissions import IsAuthenticated


class AvailableDestinationsAPIView(APIView):
    """Return the list of destinations configured in DESTINATION_BACKENDS."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        destinations = [
            {"name": dest.name, "label": dest.label}
            for dest in DestinationRegistry.get_all()
        ]
        return Response(destinations)
