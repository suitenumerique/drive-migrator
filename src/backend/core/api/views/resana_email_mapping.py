from django.conf import settings

from rest_framework.response import Response
from rest_framework.views import APIView

from ...destinations.resana.resana_backend import ResanaBackend


class ResanaEmailMappingApiView(APIView):
    """Test route for email mapping."""

    def get(self, request):
        email = request.GET["email"]
        backend = ResanaBackend()
        mapping = backend.get_mapping_from_email(email)
        return Response(
            {
                "settings.RESANA_DEFAULT_ORGANIZATION": settings.RESANA_DEFAULT_ORGANIZATION,
                "email": email,
                "mapping": {
                    "domain": mapping.domain,
                    "organization name": mapping.resana_organization_name,
                    "organization uuid": mapping.resana_organization_uuid,
                },
            }
        )
