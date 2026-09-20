"""Адреса сайта и API.

Короткие адреса сброса пароля и списка объявлений скрыты из Swagger,
чтобы не дублировать методы.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from ads.views import AdListAliasViewSet, CategoryListView, DiscussionListView
from config.views import APIDocumentationView, health
from users.views import PasswordResetConfirmAliasView, PasswordResetRequestAliasView

urlpatterns = [
    path("health/", health, name="health"),
    path("api/discussions/", DiscussionListView.as_view(), name="discussion-list"),
    path("api/categories/", CategoryListView.as_view(), name="category-list"),
    path("api/ads/", include("ads.urls")),
    path("ads/", AdListAliasViewSet.as_view({"get": "list"})),
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
    path("", include("messaging.urls")),
    path("", include("web.urls")),
]

handler403 = "web.views.permission_denied_view"
handler404 = "web.views.page_not_found_view"
handler500 = "web.views.server_error_view"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
