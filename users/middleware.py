"""Единые ограничения запросов API и HTML-форм входа и восстановления."""

import ipaddress
import json
import unicodedata

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

from users.rate_limit import request_limit_wait

API_ACTIONS = {
    "/api/users/token": "login",
    "/api/users/register": "signup",
    "/api/users/signup/resend": "signup",
    "/api/users/reset_password": "reset",
    "/users/reset_password": "reset",
    "/api/users/reset_password_confirm": "confirm",
    "/users/reset_password_confirm": "confirm",
}
WEB_ACTIONS = {
    "web:login": "login",
    "admin:login": "login",
    "web:signup": "signup",
    "web:resend-verification": "signup",
    "web:password-reset": "reset",
    "web:password-reset-resend": "reset",
    "web:password-reset-confirm": "confirm",
}
RETRY_URLS = {"login": "/login/", "signup": "/signup/", "reset": "/password-reset/"}


def client_ip(request):
    value = request.META.get("REMOTE_ADDR", "")
    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        # Доверенный nginx дописывает настоящий адрес справа. Префикс посетителя
        # в X-Forwarded-For не используем; прямой доступ к web должен быть закрыт.
        value = request.META.get("HTTP_X_FORWARDED_FOR", value).rsplit(",", 1)[-1].strip()
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return "unknown"


def request_identity(request):
    if request.resolver_match.view_name == "web:resend-verification":
        value = request.session.get("verification_email", "")
    elif request.resolver_match.view_name == "web:password-reset-resend":
        value = request.session.get("reset_email", "")
    else:
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body.decode(request.encoding or settings.DEFAULT_CHARSET))
            except ValueError, LookupError:
                data = {}
        else:
            data = request.POST
        field = "username" if request.resolver_match.view_name in {"web:login", "admin:login"} else "email"
        value = data.get(field, "") if isinstance(data, dict) else ""
    if not isinstance(value, str):
        return ""
    if request.resolver_match.view_name == "admin:login":
        value = unicodedata.normalize("NFKC", value)
    return value.strip().lower()


class AuthenticationRateLimitMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != "POST":
            return None
        api_action = API_ACTIONS.get(request.path_info.rstrip("/"))
        action = api_action or WEB_ACTIONS.get(request.resolver_match.view_name)
        if action is None:
            return None
        limits = settings.AUTH_REQUEST_LIMITS[action]
        wait = request_limit_wait(f"{action}:ip", client_ip(request), *limits["ip"])
        identity = request_identity(request)
        if not wait and identity and "identity" in limits:
            wait = request_limit_wait(f"{action}:identity", identity, *limits["identity"])
        if not wait:
            return None
        if api_action:
            response = JsonResponse({"detail": "Слишком частые запросы. Попробуйте позже."}, status=429)
        else:
            response = render(
                request,
                "errors/429.html",
                {"retry_after": wait, "retry_url": RETRY_URLS.get(action, request.path)},
                status=429,
            )
        response["Retry-After"] = str(wait)
        response["Cache-Control"] = "no-store"
        return response
