"""Рубрики: API, обязательная форма и навигация."""

import pytest

from ads.models import Ad, AdCategory

pytestmark = pytest.mark.django_db
DATA = {
    "status": "published",
    "title": "Python скрипт",
    "price": 500,
    "description": "Экспорт отчётов",
    "category": "scripts",
}


def test_public_category_list(api_client):
    response = api_client.get("/api/categories/")
    assert response.status_code == 200
    assert [item["value"] for item in response.data] == [*AdCategory.values, "discussions"]
    assert response.data[-1]["is_discussion"] is True
    assert all(not item["is_discussion"] for item in response.data[:-1])


def test_api_create_category(user_client):
    response = user_client.post("/api/ads/", {**DATA, "category": "hardware"})
    assert response.status_code == 201
    assert response.data["category"] == "hardware"
    assert response.data["category_display"] == "Железо"


@pytest.mark.parametrize("category", ["unknown", "", "discussions"])
def test_invalid_category_rejected(user_client, category):
    assert user_client.post("/api/ads/", {**DATA, "category": category}, format="json").status_code == 400
    assert not Ad.objects.exists()


def test_api_category_filter(api_client, user, ad):
    other = Ad.objects.create(**{**DATA, "category": "hardware"}, author=user)
    response = api_client.get("/api/ads/", {"category": "hardware"})
    assert [item["id"] for item in response.data["results"]] == [other.pk]


def test_category_page_only_matching(client, user, ad):
    other = Ad.objects.create(**{**DATA, "category": "courses"}, author=user)
    response = client.get("/category/courses/")
    assert list(response.context["ads"]) == [other]
    assert 'aria-current="page"' in response.content.decode()
    assert 'class="header-search" action="/category/courses/"' in response.content.decode()


def test_search_inside_category(client, user):
    found = Ad.objects.create(**DATA, author=user)
    Ad.objects.create(**{**DATA, "category": "hardware"}, author=user)
    Ad.objects.create(**{**DATA, "title": "Java"}, author=user)
    assert list(client.get("/category/scripts/", {"search": "Python"}).context["ads"]) == [found]


def test_pagination_inside_category(client, user):
    ads = [Ad.objects.create(**DATA, author=user) for _ in range(6)]
    response = client.get("/category/scripts/", {"search": "Python", "page": 2})
    assert list(response.context["ads"]) == ads[1::-1]
    assert "search=Python" in response.content.decode()


def test_category_badge(client, ad):
    html = client.get("/").content.decode()
    assert "category-badge" in html
    assert ad.get_category_display() in html
    assert "/category/scripts/" in client.get(f"/ad/{ad.pk}/").content.decode()


def test_unknown_category_404(client):
    assert client.get("/category/missing/").status_code == 404
