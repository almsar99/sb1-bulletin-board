"""Объявления: данные, доступ, сортировка и страницы."""

import pytest
from django.utils import timezone

from ads.models import Ad

pytestmark = pytest.mark.django_db
URL = "/api/ads/"
DATA = {"title": "Стол", "price": 1000, "description": "Деревянный стол"}


@pytest.mark.parametrize("url", [URL, "/ads/"])
def test_public_list(api_client, ad, url):
    response = api_client.get(url)
    assert response.status_code == 200
    assert response.data["results"][0]["id"] == ad.pk
    assert response.data["results"][0]["author"]["id"] == ad.author_id


def test_create_sets_author(user_client, user, other_user):
    response = user_client.post(URL, {**DATA, "author": other_user.pk}, format="json")
    assert response.status_code == 201
    assert Ad.objects.get(pk=response.data["id"]).author == user


@pytest.mark.parametrize("field,value", [("price", -1), ("price", 1.5), ("title", ""), ("title", "x" * 201)])
def test_invalid_data(user_client, field, value):
    assert user_client.post(URL, {**DATA, field: value}, format="json").status_code == 400


def test_zero_price_allowed(user_client):
    assert user_client.post(URL, {**DATA, "price": 0}).status_code == 201


def test_ads_page_size(api_client, user):
    items = [Ad.objects.create(**DATA, author=user) for _ in range(7)]
    first = api_client.get("/ads/?page_size=100").data
    second = api_client.get("/ads/?page=2").data
    assert first["count"] == 7
    assert len(first["results"]) == 4
    ids = [item["id"] for item in first["results"] + second["results"]]
    assert ids == [item.pk for item in reversed(items)]


def test_equal_timestamps_are_stable(api_client, user):
    items = [Ad.objects.create(**DATA, author=user) for _ in range(6)]
    Ad.objects.update(created_at=timezone.now())
    result = api_client.get(URL).data["results"] + api_client.get(URL + "?page=2").data["results"]
    assert [item["id"] for item in result] == [item.pk for item in reversed(items)]
