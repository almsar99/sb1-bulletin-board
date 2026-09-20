"""Закрытый справочник рубрик для навигации."""

from django.conf import settings

from ads.models import Ad, AdCategory, AdStatus


def categories(request):
    return {"categories": AdCategory.choices, "category_values": AdCategory.values}


def site_metadata(request):
    site_url = settings.FRONTEND_URL.rstrip("/")
    return {"site_url": site_url, "brand_name": "маркет.код", "page_url": site_url + request.path}


def review_queue(request):
    user = getattr(request, "user", None)
    if user and user.is_authenticated and user.is_admin:
        return {"review_queue_count": Ad.objects.filter(status=AdStatus.DRAFT).count()}
    return {}
