from django.http import HttpResponse, Http404
from core.models import User
from django.contrib.auth import authenticate, login
from django.conf import settings

def spoof_view(request):
    if not settings.DEBUG:
        raise Http404()
    email = request.GET.get('user')
    user = User.objects.get(email=email)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return HttpResponse("Done !")
