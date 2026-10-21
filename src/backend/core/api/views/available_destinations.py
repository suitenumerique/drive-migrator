"""Available destinations endpoints."""

from django.http import Http404

from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.destination_accounts import check_user_account
from core.api.permissions import IsAuthenticated
from core.backends.destination import AccountPolicy, DestinationRegistry


class AvailableDestinationsAPIView(APIView):
    """Return the list of destinations configured in DESTINATION_BACKENDS."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        destinations = [
            {
                "name": dest.name,
                "label": dest.label,
                "account_policy": dest.get_account_policy(),
                "account_creation_url": dest.get_account_creation_url(),
            }
            for dest in DestinationRegistry.get_all()
        ]
        return Response(destinations)


class DestinationAccountStatusAPIView(APIView):
    """Tell whether the user has the account a destination requires them to create."""

    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        try:
            dest = DestinationRegistry.get(name)
        except ValueError as error:
            raise Http404 from error
        if dest.get_account_policy() != AccountPolicy.USER_CREATED:
            return Response({"exists": True})
        return Response({"exists": check_user_account(dest, request.user)})
