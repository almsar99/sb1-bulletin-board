"""HTTP-границы JWT: подпись, срок, источник токена и актуальное состояние пользователя."""

from datetime import timedelta
from urllib.parse import urlencode

import jwt
import pytest
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from ads.models import Ad
from tests.conftest import PASSWORD
from users.models import User, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_pair(api_client, user):
    response = api_client.post("/api/users/token/", {"email": user.email, "password": PASSWORD}, format="json")
    assert response.status_code == 200
    assert {"access", "refresh"} <= response.data.keys()
    return api_client, user, response.data


@pytest.mark.parametrize("token_type", ["access", "refresh"])
def test_alg_none_rejected(signed_pair, token_type):
    client, _, pair = signed_pair
    token_class = AccessToken if token_type == "access" else RefreshToken
    token = token_class(pair[token_type])
    unsigned = jwt.encode(dict(token.payload), key="", algorithm="none")
    if token_type == "access":
        response = client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {unsigned}")
    else:
        response = client.post("/api/users/token/refresh/", {"refresh": unsigned}, format="json")
    assert response.status_code == 401
    assert response.data["code"] == "token_not_valid"


def test_expired_access_rejected(signed_pair):
    client, _, pair = signed_pair
    token = AccessToken(pair["access"])
    token.set_exp(lifetime=timedelta(seconds=-1))
    response = client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 401
    assert response.data["code"] == "token_not_valid"


@pytest.mark.parametrize("source", ["query", "cookie", "Basic", "Token", "bearer"])
def test_access_only_from_bearer_header(signed_pair, source):
    client, account, pair = signed_pair
    if source == "query":
        response = client.get(
            "/api/users/me/", {"access": pair["access"], "access_token": pair["access"], "token": pair["access"]}
        )
    elif source == "cookie":
        for name in ("access", "access_token", "jwt", "token"):
            client.cookies[name] = pair["access"]
        response = client.get("/api/users/me/")
    else:
        response = client.get("/api/users/me/", HTTP_AUTHORIZATION=f"{source} {pair['access']}")
    assert response.status_code == 401
    control = client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {pair['access']}")
    assert control.status_code == 200
    assert control.data["id"] == account.pk


@pytest.mark.parametrize("source", ["query", "header", "cookie"])
def test_refresh_requires_body_field(signed_pair, source):
    client, _, pair = signed_pair
    kwargs = {}
    if source == "query":
        kwargs["QUERY_STRING"] = urlencode({"refresh": pair["refresh"]})
    elif source == "header":
        kwargs["HTTP_AUTHORIZATION"] = f"Bearer {pair['refresh']}"
    else:
        client.cookies["refresh"] = pair["refresh"]
        client.cookies["refresh_token"] = pair["refresh"]
    response = client.post("/api/users/token/refresh/", {}, format="json", **kwargs)
    assert response.status_code == 400
    assert "refresh" in response.data
    assert "access" not in response.data
    control = client.post("/api/users/token/refresh/", {"refresh": pair["refresh"]}, format="json")
    assert control.status_code == 200
    assert "access" in control.data


def test_deactivation_revokes_access_refresh_and_verify(signed_pair):
    client, account, pair = signed_pair
    assert client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {pair['access']}").status_code == 200
    User.objects.filter(pk=account.pk).update(is_active=False)
    assert client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {pair['access']}").status_code == 401
    assert client.post("/api/users/token/refresh/", {"refresh": pair["refresh"]}, format="json").status_code == 401
    assert client.post("/api/users/token/verify/", {"token": pair["access"]}, format="json").status_code == 401
    assert (
        client.post("/api/users/token/", {"email": account.email, "password": PASSWORD}, format="json").status_code
        == 401
    )


def test_role_is_loaded_from_database_not_claim(signed_pair, other_user):
    client, account, pair = signed_pair
    ad = Ad.objects.create(author=other_user, title="Чужое объявление", description="Описание", price=1)
    url = f"/api/ads/{ad.pk}/"
    original_token = pair["access"]
    User.objects.filter(pk=account.pk).update(role=UserRole.ADMIN, is_staff=False)
    response = client.patch(
        url, {"title": "Правка администратора"}, format="json", HTTP_AUTHORIZATION=f"Bearer {original_token}"
    )
    assert response.status_code == 200
    User.objects.filter(pk=account.pk).update(role=UserRole.USER, is_staff=True)
    response = client.patch(
        url, {"title": "Запрещённая правка"}, format="json", HTTP_AUTHORIZATION=f"Bearer {original_token}"
    )
    assert response.status_code == 403
    claimed_admin = AccessToken(original_token)
    claimed_admin["role"] = UserRole.ADMIN
    response = client.patch(
        url, {"title": "Подмена роли в JWT"}, format="json", HTTP_AUTHORIZATION=f"Bearer {claimed_admin}"
    )
    assert response.status_code == 403
    ad.refresh_from_db()
    assert ad.title == "Правка администратора"
