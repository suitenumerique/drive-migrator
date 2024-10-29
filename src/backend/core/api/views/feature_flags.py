from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import FeatureFlag
from core.utils import is_feature


class FeatureFlagsApiView(APIView):
    """Synchronize API view."""

    def get(self, request):
        flags = {}
        for flag, _text in FeatureFlag.Name.choices:
            flags[flag] = is_feature(flag)
        return Response(flags)
