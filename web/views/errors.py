"""Страницы ошибок сайта."""

from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import requires_csrf_token


@never_cache
@requires_csrf_token
def csrf_failure(request, reason=""):
    """Предложить безопасный GET вместо повторной отправки отклонённой формы."""
    retry_views = {
        "web:login": "web:login",
        "admin:login": "admin:login",
        "web:signup": "web:signup",
        "web:resend-verification": "web:signup-check-email",
        "web:password-reset": "web:password-reset",
        "web:password-reset-resend": "web:password-reset",
        "web:password-reset-confirm": "web:password-reset",
    }
    view_name = request.resolver_match.view_name if request.resolver_match else ""
    fallback = "web:account" if request.user.is_authenticated else "web:login"
    retry_url = reverse(retry_views.get(view_name, fallback))
    return render(request, "errors/csrf.html", {"retry_url": retry_url}, status=403)


def permission_denied_view(request, exception):
    return render(request, "errors/403.html", status=403)


def page_not_found_view(request, exception):
    return render(request, "errors/404.html", status=404)


def server_error_view(request):
    return render(request, "errors/500.html", status=500)
