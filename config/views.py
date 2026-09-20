"""Готовность приложения и публичная документация API."""

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.csp import csp_override
from django.views.decorators.http import require_GET
from drf_spectacular.views import SpectacularSwaggerView


class APIDocumentationView(SpectacularSwaggerView):
    """Разрешение CDN Swagger ограничено страницей документации."""

    def dispatch(self, request, *args, **kwargs):
        policy = settings.SECURE_CSP.copy()
        if policy:
            for directive in ("script-src", "style-src", "img-src"):
                policy[directive] = [
                    *policy.get(directive, policy.get("default-src", [])),
                    "https://cdn.jsdelivr.net",
                ]
        return csp_override(policy)(super().dispatch)(request, *args, **kwargs)


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
