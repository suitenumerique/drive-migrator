from rest_framework.views import APIView

from .. import APIException


class ErrorApiView(APIView):
    """Error test route."""

    def get(self, request):
        raise APIException(name="ErrorApi")
