from django.conf import settings
from django.http import FileResponse
from django.urls import include, path


def dashboard(request):
    return FileResponse(open(settings.BASE_DIR / "frontend" / "index.html", "rb"), content_type="text/html")


urlpatterns = [path("", dashboard), path("api/", include("traffic.urls"))]
