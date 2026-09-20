"""Одинаковая видимость объявлений и вложенных записей в API и на страницах."""

import pytest
from rest_framework.test import APIClient

from ads.models import AdStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def draft(ad):
    ad.status = AdStatus.DRAFT
    ad.save()
    return ad


def test_anonymous_list_hides_draft(api_client, client, draft):
    assert api_client.get("/api/ads/").data["count"] == 0
    assert not client.get("/").context["ads"]


def test_other_user_list_hides_draft(other_client, client, other_user, draft):
    client.force_login(other_user)
    assert other_client.get("/api/ads/").data["count"] == 0
    assert not client.get("/").context["ads"]


def test_owner_sees_draft(client, user, user_client, draft):
    client.force_login(user)
    assert list(client.get("/account/").context["ads"]) == [draft]
    assert "На рассмотрении" in client.get("/account/").content.decode()
    assert user_client.get(f"/api/ads/{draft.pk}/").status_code == 200
    assert client.get(f"/ad/{draft.pk}/").status_code == 200


def test_admin_sees_draft(client, admin, admin_client, draft):
    client.force_login(admin)
    assert admin_client.get("/api/ads/").data["count"] == 1
    assert client.get(f"/ad/{draft.pk}/").status_code == 200


@pytest.mark.parametrize(
    "path", ["/ad/{id}/", "/ad/{id}/edit/", "/ad/{id}/delete/", "/api/ads/{id}/", "/api/ads/{id}/reviews/"]
)
def test_other_draft_returns_404(client, other_user, draft, path):
    browser = APIClient()
    browser.force_authenticate(other_user)
    client.force_login(other_user)
    response = (browser if path.startswith("/api/") else client).get(path.format(id=draft.pk))
    assert response.status_code == 404


def test_anonymous_detail_404(client, draft):
    assert client.get(f"/ad/{draft.pk}/").status_code == 404


def test_admin_publishes_draft(admin_client, draft, api_client):
    assert admin_client.patch(f"/api/ads/{draft.pk}/", {"status": "published"}).status_code == 200
    assert api_client.get("/api/ads/").data["count"] == 1


def test_author_archives_via_web(client, user, ad):
    client.force_login(user)
    assert client.post(f"/ad/{ad.pk}/status/", {"status": "archived"}).status_code == 302
    ad.refresh_from_db()
    assert ad.status == "archived"


def test_archived_not_public(api_client, client, ad):
    ad.status = "archived"
    ad.save()
    assert api_client.get("/api/ads/").data["count"] == 0
    assert not client.get("/").context["ads"]


def test_search_cannot_find_draft(api_client, client, draft):
    assert api_client.get("/api/ads/", {"search": draft.title}).data["count"] == 0
    assert not client.get("/", {"search": draft.title}).context["ads"]


def test_filters_cannot_find_draft(api_client, client, draft):
    assert api_client.get("/api/ads/", {"category": draft.category, "price_min": 0}).data["count"] == 0
    assert not client.get(f"/category/{draft.category}/").context["ads"]


@pytest.mark.parametrize("status,code", [("published", 403), ("draft", 404), ("archived", 404)])
def test_other_cannot_change_status(other_client, client, other_user, ad, status, code):
    ad.status = status
    ad.save()
    client.force_login(other_user)
    assert other_client.patch(f"/api/ads/{ad.pk}/", {"status": "published"}).status_code == code
    assert client.post(f"/ad/{ad.pk}/status/", {"status": "published"}).status_code == code
    ad.refresh_from_db()
    assert ad.status == status


def test_status_action_post_and_validation(client, user, ad):
    client.force_login(user)
    assert client.get(f"/ad/{ad.pk}/status/").status_code == 405
    assert client.post(f"/ad/{ad.pk}/status/", {"status": "invalid"}).status_code == 400
    ad.refresh_from_db()
    assert ad.status == "published"
