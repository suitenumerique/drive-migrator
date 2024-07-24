from django.conf import settings
from django.contrib.auth import login
from django.http import Http404, HttpResponse

from core.models import User


def spoof_view(request):
    if not settings.DEBUG:
        raise Http404()
    email = request.GET.get("user")
    user = User.objects.get(email=email)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponse("Done !")
