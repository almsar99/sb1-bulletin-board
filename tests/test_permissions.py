"""Матрица прав для анонима, постороннего, автора и администратора."""

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from ads.models import Ad, Review
from tests.conftest import PASSWORD
from users.models import User, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["anonymous", "other_user", "user", "admin"])
def actor(request):
    client = APIClient()
    if request.param != "anonymous":
        account = request.getfixturevalue(request.param)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(account).access_token}")
    return request.param, client


@pytest.mark.parametrize("action", ["list", "create", "retrieve", "update", "partial_update", "destroy"])
def test_ad_permissions(request, actor, action, ad):
    role, client = actor
    methods = {
        "list": "get",
        "create": "post",
        "retrieve": "get",
        "update": "put",
        "partial_update": "patch",
        "destroy": "delete",
    }
    url = "/api/ads/" + (f"{ad.pk}/" if action not in ["list", "create"] else "")
    before = Ad.objects.filter(pk=ad.pk).values().get()
    payload = {"title": "Название", "price": 100, "description": "Описание"}
    response = getattr(client, methods[action])(url, payload, format="json")
    if role == "anonymous" and action != "list":
        expected = 401
    elif role == "other_user" and action in ["update", "partial_update", "destroy"]:
        expected = 403
    else:
        expected = {"create": 201, "destroy": 204}.get(action, 200)
    assert response.status_code == expected
    if expected in (401, 403):
        assert Ad.objects.count() == 1
        assert Ad.objects.filter(pk=ad.pk).values().get() == before
    elif action == "destroy":
        assert not Ad.objects.filter(pk=ad.pk).exists()
    elif action in ("create", "update", "partial_update"):
        saved = Ad.objects.get(pk=response.data["id"])
        for field, value in payload.items():
            assert getattr(saved, field) == value
            assert response.data[field] == value
        if action == "create":
            assert saved.author == request.getfixturevalue(role)
        else:
            assert saved.author_id == before["author_id"]
    else:
        result = response.data["results"][0] if action == "list" else response.data
        assert result["id"] == ad.pk
        assert result["title"] == ad.title
        assert result["price"] == ad.price
        assert result["description"] == ad.description
        assert result["author"]["id"] == ad.author_id


@pytest.mark.parametrize("action", ["list", "create", "retrieve", "partial_update", "destroy"])
def test_review_permissions(request, actor, action, ad, review):
    role, client = actor
    methods = {"list": "get", "create": "post", "retrieve": "get", "partial_update": "patch", "destroy": "delete"}
    url = f"/api/ads/{ad.pk}/reviews/" + (f"{review.pk}/" if action not in ["list", "create"] else "")
    before = Review.objects.filter(pk=review.pk).values().get()
    response = getattr(client, methods[action])(url, {"text": "Отзыв"}, format="json")
    if role == "anonymous":
        expected = 401
    elif role == "other_user" and action in ["partial_update", "destroy"]:
        expected = 403
    else:
        expected = {"create": 201, "destroy": 204}.get(action, 200)
    assert response.status_code == expected
    if expected in (401, 403):
        assert Review.objects.count() == 1
        assert Review.objects.filter(pk=review.pk).values().get() == before
    elif action == "destroy":
        assert not Review.objects.filter(pk=review.pk).exists()
    elif action in ("create", "partial_update"):
        saved = Review.objects.get(pk=response.data["id"])
        assert saved.text == response.data["text"] == "Отзыв"
        assert saved.ad_id == ad.pk
        if action == "create":
            assert saved.author == request.getfixturevalue(role)
        else:
            assert saved.author_id == before["author_id"]
    else:
        result = response.data["results"][0] if action == "list" else response.data
        assert result["id"] == review.pk
        assert result["text"] == review.text
        assert result["ad"] == ad.pk
        assert result["author"]["id"] == review.author_id


@pytest.mark.parametrize("url", ["/api/ads/99999/", "/api/ads/99999/reviews/1/"])
def test_missing_resources(actor, url):
    role, client = actor
    assert client.get(url).status_code == (401 if role == "anonymous" else 404)


@pytest.mark.parametrize("role,is_staff", [(UserRole.ADMIN, False), (UserRole.USER, True)])
@pytest.mark.parametrize(
    "resource,method", [("ad", "put"), ("ad", "patch"), ("ad", "delete"), ("review", "patch"), ("review", "delete")]
)
def test_jwt_permissions_use_role_independently_of_staff(api_client, ad, review, role, is_staff, resource, method):
    account = User.objects.create_user(email="role-check@example.com", password=PASSWORD, role=role, is_staff=is_staff)
    login = api_client.post("/api/users/token/", {"email": account.email, "password": PASSWORD}, format="json")
    assert login.status_code == 200
    assert login.data["access"] and login.data["refresh"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    url = f"/api/ads/{ad.pk}/"
    payload = {"title": "Изменено через JWT", "price": 100, "description": "Новое описание"}
    target = ad
    if resource == "review":
        url += f"reviews/{review.pk}/"
        payload = {"text": "Изменено через JWT"}
        target = review

    response = getattr(api_client, method)(url, payload, format="json")

    if role == UserRole.USER:
        assert response.status_code == 403
        target.refresh_from_db()
        assert target.title == "Велосипед" if resource == "ad" else target.text == "Отличное предложение"
    elif method == "delete":
        assert response.status_code == 204
        assert not type(target).objects.filter(pk=target.pk).exists()
    else:
        assert response.status_code == 200
        target.refresh_from_db()
        for field, value in payload.items():
            assert getattr(target, field) == value
