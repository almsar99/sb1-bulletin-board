"""Адреса сайта и API.

Короткие адреса сброса пароля скрыты из Swagger, чтобы не дублировать методы.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from config.views import APIDocumentationView, health
from users.views import PasswordResetConfirmAliasView, PasswordResetRequestAliasView

urlpatterns = [
    path("health/", health, name="health"),
    path("users/reset_password/", PasswordResetRequestAliasView.as_view()),
    path("users/reset_password_confirm", PasswordResetConfirmAliasView.as_view()),
    path("users/reset_password_confirm/", PasswordResetConfirmAliasView.as_view()),
    path("admin/", admin.site.urls),
    path("api/users/", include("users.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        APIDocumentationView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
