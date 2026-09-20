"""Комбинации поиска, фильтров и постраничного вывода."""

import pytest
from django.utils import timezone

from ads.models import Ad

pytestmark = pytest.mark.django_db
URL = "/api/ads/"


@pytest.fixture
def items(user):
    return [
        Ad.objects.create(title=title, price=price, description="Описание", author=user)
        for title, price in [("Велосипед", 100), ("ВЕЛОШЛЕМ", 300), ("Стол", 500), ("Велонасос", 700)]
    ]


@pytest.mark.parametrize("search,count", [("вело", 3), ("ВЕЛО", 3), ("лоси", 1), ("телевизор", 0), ("", 4)])
def test_search_case_and_substring(api_client, items, search, count):
    response = api_client.get(URL, {"search": search})
    assert response.status_code == 200
    assert response.data["count"] == count


@pytest.mark.parametrize(
    "params,prices",
    [
        ({"price_min": 300}, [300, 500, 700]),
        ({"price_max": 300}, [100, 300]),
        ({"price_min": 300, "price_max": 500}, [300, 500]),
        ({"price_min": 500, "price_max": 300}, []),
        ({"search": "вело", "price_min": 200, "price_max": 600}, [300]),
    ],
)
def test_price_filters(api_client, items, params, prices):
    response = api_client.get(URL, {**params, "ordering": "price"})
    assert response.status_code == 200
    assert [item["price"] for item in response.data["results"]] == prices


@pytest.mark.parametrize("ordering", ["price", "-price", "created_at", "-created_at"])
def test_allowed_ordering(api_client, items, ordering):
    response = api_client.get(URL, {"ordering": ordering})
    expected = items[::-1] if ordering.startswith("-") else items
    assert [item["id"] for item in response.data["results"]] == [item.pk for item in expected]


@pytest.mark.parametrize("ordering", ["price", "-price", "created_at", "-created_at"])
def test_ties_across_pages(api_client, user, ordering):
    items = [Ad.objects.create(title="Велосипед", price=100, author=user) for _ in range(11)]
    Ad.objects.update(created_at=timezone.now())
    Ad.objects.create(title="Стол", price=100, author=user)
    ids = []
    for page in range(1, 4):
        response = api_client.get(URL, {"search": "вело", "ordering": ordering, "page": page})
        assert response.status_code == 200
        ids.extend(item["id"] for item in response.data["results"])
    expected = items[::-1] if ordering.startswith("-") else items
    assert ids == [item.pk for item in expected]
    assert len(set(ids)) == 11


def test_forbidden_ordering_falls_back(api_client, items):
    response = api_client.get(URL, {"ordering": "author__email"})
    assert response.status_code == 200
    assert [item["id"] for item in response.data["results"]] == [item.pk for item in reversed(items)]


@pytest.mark.parametrize("field,value", [("price_min", "x"), ("price_max", -1)])
def test_invalid_price_filter(api_client, field, value):
    assert api_client.get(URL, {field: value}).status_code == 400


def test_alias_supports_search(api_client, items):
    response = api_client.get("/ads/", {"search": "шлем"})
    assert response.status_code == 200
    assert response.data["count"] == 1
