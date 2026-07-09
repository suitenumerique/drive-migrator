from django.conf import settings

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class MigrationConfigApiView(APIView):
    """Expose migration-related settings the frontend needs to display to users."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "file_limit_per_workspace": settings.MIGRATION_FILE_LIMIT_PER_WORKSPACE,
            }
        )
