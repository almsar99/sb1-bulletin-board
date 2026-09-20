from django.urls import path
from rest_framework.routers import SimpleRouter

from ads.views import AdViewSet, ReviewViewSet

app_name = "ads"
router = SimpleRouter()
router.register("", AdViewSet, basename="ad")
urlpatterns = [
    path("<int:ad_id>/reviews/", ReviewViewSet.as_view({"get": "list", "post": "create"}), name="review-list"),
    path(
        "<int:ad_id>/reviews/<int:pk>/",
        ReviewViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="review-detail",
    ),
] + router.urls
