"""Отзыв JWT при смене пароля, совместимость и одновременная выдача."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from tests.conftest import PASSWORD
from users.authentication import PasswordAwareRefreshSerializer, PasswordAwareRefreshToken
from users.models import User
from users.serializers import PasswordChangeSerializer, UserSerializer
from users.tokens import make_reset_credentials

pytestmark = pytest.mark.django_db
NEW_PASSWORD = "New-Strong-Passphrase-297!"


def issue(client, user, password=PASSWORD):
    response = client.post("/api/users/token/", {"email": user.email, "password": password})
    assert response.status_code == 200
    return response.json()


def profile(client, access):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    result = client.get("/api/users/me/")
    client.credentials()
    return result


@pytest.mark.parametrize("access", ["not-a-jwt", "invalid.signature.value"])
def test_malformed_access_cannot_open_profile(api_client, access):
    response = profile(api_client, access)
    assert response.status_code == 401
    assert response.json()["code"] == "token_not_valid"


def test_tampered_access_cannot_open_profile(api_client, user):
    tokens = issue(api_client, user)
    header, payload, signature = tokens["access"].split(".")
    signature = ("a" if signature[0] != "a" else "b") + signature[1:]
    response = profile(api_client, f"{header}.{payload}.{signature}")
    assert response.status_code == 401
    assert response.json()["code"] == "token_not_valid"


def test_expired_refresh_rejected(api_client, user):
    tokens = issue(api_client, user)
    refresh = RefreshToken(tokens["refresh"])
    refresh.set_exp(lifetime=timedelta(seconds=-1))
    response = api_client.post("/api/users/token/refresh/", {"refresh": str(refresh)}, format="json")
    assert response.status_code == 401
    assert response.json()["code"] == "token_not_valid"
    assert "access" not in response.json()


def change_password(flow, api_client, client, user, tokens):
    if flow == "change":
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = api_client.post(
            "/api/users/set_password/", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        api_client.credentials()
        assert response.status_code == 204
    else:
        user.refresh_from_db()
        uid, token = make_reset_credentials(user)
        if flow == "api_reset":
            response = api_client.post(
                "/api/users/reset_password_confirm/", {"uid": uid, "token": token, "new_password": NEW_PASSWORD}
            )
            assert response.status_code == 204
        else:
            response = client.get(reverse("web:password-reset-confirm", args=[uid, token]), follow=True)
            response = client.post(
                response.redirect_chain[-1][0], {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}
            )
            assert response.status_code == 302 and response.url == "/password-reset/complete/"


@pytest.mark.parametrize("flow", ["change", "api_reset", "web_reset"])
def test_all_password_flows_revoke_tokens(api_client, client, user, flow):
    tokens = issue(api_client, user)
    change_password(flow, api_client, client, user, tokens)
    user.refresh_from_db()
    assert user.password_changed_at is not None
    assert user.check_password(NEW_PASSWORD)
    responses = [
        profile(api_client, tokens["access"]),
        api_client.post("/api/users/token/refresh/", {"refresh": tokens["refresh"]}),
        api_client.post("/api/users/token/verify/", {"token": tokens["access"]}),
        api_client.post("/api/users/token/verify/", {"token": tokens["refresh"]}),
    ]
    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": "Недействительный токен.", "code": "token_not_valid"}
    new_tokens = issue(api_client, user, NEW_PASSWORD)
    assert profile(api_client, new_tokens["access"]).status_code == 200
    refreshed = api_client.post("/api/users/token/refresh/", {"refresh": new_tokens["refresh"]})
    assert refreshed.status_code == 200
    assert profile(api_client, refreshed.json()["access"]).status_code == 200
    assert api_client.post("/api/users/token/verify/", {"token": new_tokens["refresh"]}).status_code == 200


@pytest.mark.parametrize(
    "flow,method,payload_kind",
    [
        ("change", "patch", "name"),
        ("api_reset", "put", "name"),
        ("web_reset", "patch", "empty"),
        ("change", "put", "read_only"),
    ],
)
def test_profile_preserves_password(api_client, client, user, flow, method, payload_kind):
    tokens = issue(api_client, user)
    initial_name = user.first_name
    payload = {
        "name": {"first_name": "Обновлённое имя"},
        "empty": {},
        "read_only": {"id": user.pk + 100, "email": "other@example.com", "role": "admin"},
    }[payload_kind]
    reset_client = APIClient()
    original_update = UserSerializer.update
    password_state = {}

    def change_password_before_profile_save(serializer, instance, validated_data):
        # HTTP-запрос профиля уже прошёл JWT-аутентификацию со старым паролем.
        assert instance.password_changed_at is None
        assert instance.check_password(PASSWORD)
        change_password(flow, reset_client, client, user, tokens)
        user.refresh_from_db()
        password_state.update(password=user.password, timestamp=user.password_changed_at)
        assert password_state["timestamp"] is not None
        assert user.check_password(NEW_PASSWORD)
        assert profile(reset_client, tokens["access"]).status_code == 401
        assert reset_client.post("/api/users/token/refresh/", {"refresh": tokens["refresh"]}).status_code == 401
        return original_update(serializer, instance, validated_data)

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    with patch.object(UserSerializer, "update", change_password_before_profile_save):
        response = getattr(api_client, method)("/api/users/me/", payload, format="json")
    api_client.credentials()

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.password == password_state["password"]
    assert user.password_changed_at == password_state["timestamp"]
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(PASSWORD)
    assert user.first_name == (payload["first_name"] if payload_kind == "name" else initial_name)
    assert response.data["first_name"] == user.first_name
    assert response.data["id"] == user.pk
    assert response.data["email"] == user.email
    assert response.data["role"] == user.role == "user"
    assert profile(api_client, tokens["access"]).status_code == 401
    assert api_client.post("/api/users/token/refresh/", {"refresh": tokens["refresh"]}).status_code == 401
    assert api_client.post("/api/users/token/", {"email": user.email, "password": PASSWORD}).status_code == 401
    new_tokens = issue(api_client, user, NEW_PASSWORD)
    assert profile(api_client, new_tokens["access"]).status_code == 200


@pytest.mark.parametrize("reset_flow", ["api_reset", "web_reset"])
def test_inflight_password_change_cannot_undo_reset(api_client, client, user, reset_flow):
    tokens = issue(api_client, user)
    reset_client = APIClient()
    original_save = PasswordChangeSerializer.save
    attempted_password = "Outdated-Request-Password-2026!"
    password_state = {}

    def reset_before_password_save(serializer, **kwargs):
        # current_password уже принят сериализатором до конкурентного reset.
        assert serializer.validated_data["current_password"] == PASSWORD
        change_password(reset_flow, reset_client, client, user, tokens)
        user.refresh_from_db()
        password_state.update(password=user.password, timestamp=user.password_changed_at)
        assert user.check_password(NEW_PASSWORD)
        return original_save(serializer, **kwargs)

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    with patch.object(PasswordChangeSerializer, "save", reset_before_password_save):
        response = api_client.post(
            "/api/users/set_password/",
            {"current_password": PASSWORD, "new_password": attempted_password},
            format="json",
        )
    api_client.credentials()

    assert response.status_code == 400
    assert "current_password" in response.data
    user.refresh_from_db()
    assert user.password == password_state["password"]
    assert user.password_changed_at == password_state["timestamp"]
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(attempted_password)
    assert not user.check_password(PASSWORD)
    assert profile(api_client, tokens["access"]).status_code == 401
    assert api_client.post("/api/users/token/refresh/", {"refresh": tokens["refresh"]}).status_code == 401
    new_tokens = issue(api_client, user, NEW_PASSWORD)
    assert profile(api_client, new_tokens["access"]).status_code == 200


@pytest.mark.parametrize("account_state", ["inactive", "deleted"])
def test_inflight_password_change_rechecks_account_state(api_client, user, account_state):
    tokens = issue(api_client, user)
    original_save = PasswordChangeSerializer.save
    original_hash = user.password

    def revoke_account_before_save(serializer, **kwargs):
        if account_state == "inactive":
            User.objects.filter(pk=user.pk).update(is_active=False)
        else:
            User.objects.filter(pk=user.pk).delete()
        return original_save(serializer, **kwargs)

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    with patch.object(PasswordChangeSerializer, "save", revoke_account_before_save):
        response = api_client.post(
            "/api/users/set_password/",
            {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )
    api_client.credentials()

    assert response.status_code == 401
    if account_state == "inactive":
        user.refresh_from_db()
        assert not user.is_active
        assert user.password == original_hash
        assert user.password_changed_at is None
    else:
        assert not User.objects.filter(pk=user.pk).exists()


def test_issue_before_and_after_change_within_one_second(api_client, user):
    before = (timezone.now() - timedelta(minutes=1)).replace(microsecond=100_000)
    with patch("rest_framework_simplejwt.tokens.aware_utcnow", return_value=before):
        old = issue(api_client, user)
    with patch("users.models.timezone.now", return_value=before + timedelta(microseconds=1)):
        user.set_password(NEW_PASSWORD)
        user.save(update_fields=["password"])
    with patch("rest_framework_simplejwt.tokens.aware_utcnow", return_value=before + timedelta(microseconds=2)):
        new = issue(api_client, user, NEW_PASSWORD)
    assert AccessToken(old["access"])["iat"] == AccessToken(new["access"])["iat"]
    assert profile(api_client, old["access"]).status_code == 401
    assert profile(api_client, new["access"]).status_code == 200
    assert api_client.post("/api/users/token/refresh/", {"refresh": new["refresh"]}).status_code == 200


def test_legacy_tokens_work_until_password_changes(api_client, user):
    refresh = RefreshToken.for_user(user)
    access = str(refresh.access_token)
    assert user.password_changed_at is None
    assert profile(api_client, access).status_code == 200
    renewed = api_client.post("/api/users/token/refresh/", {"refresh": str(refresh)})
    assert renewed.status_code == 200
    user.set_password(NEW_PASSWORD)
    user.save(update_fields=["password"])
    assert profile(api_client, access).status_code == 401
    assert profile(api_client, renewed.json()["access"]).status_code == 401
    assert api_client.post("/api/users/token/refresh/", {"refresh": str(refresh)}).status_code == 401


def test_login_started_before_reset_cannot_issue_working_token(api_client, user):
    stale_user = User.objects.get(pk=user.pk)
    user.set_password(NEW_PASSWORD)
    user.save(update_fields=["password"])
    refresh = PasswordAwareRefreshToken.for_user(stale_user)
    assert profile(api_client, str(refresh.access_token)).status_code == 401
    assert api_client.post("/api/users/token/refresh/", {"refresh": str(refresh)}).status_code == 401


@pytest.mark.parametrize("token_class", [RefreshToken, PasswordAwareRefreshToken])
def test_refresh_racing_with_reset_cannot_restore_access(api_client, user, token_class):
    refresh = token_class.for_user(user)
    original = TokenRefreshSerializer.validate

    def reset_before_issue(serializer, attrs):
        user.set_password(NEW_PASSWORD)
        user.save(update_fields=["password"])
        return original(serializer, attrs)

    with patch.object(TokenRefreshSerializer, "validate", reset_before_issue):
        data = PasswordAwareRefreshSerializer().validate({"refresh": str(refresh)})
    assert profile(api_client, data["access"]).status_code == 401


def test_automatic_hash_upgrade_does_not_revoke_tokens(api_client, user, settings):
    user.set_password(NEW_PASSWORD)
    user.save(update_fields=["password"])
    stamp = user.password_changed_at
    tokens = issue(api_client, user, NEW_PASSWORD)
    settings.PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.PBKDF2PasswordHasher",
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]
    assert user.check_password(NEW_PASSWORD)
    user.refresh_from_db()
    assert user.password.startswith("pbkdf2_sha256$")
    assert user.password_changed_at == stamp
    assert profile(api_client, tokens["access"]).status_code == 200


def test_unrelated_or_empty_save_does_not_write_pending_password(user):
    user.set_password(NEW_PASSWORD)
    user.save(update_fields=[])
    user.first_name = "Новое имя"
    user.save(update_fields=["first_name"])
    stored = User.objects.get(pk=user.pk)
    assert stored.password_changed_at is None
    assert stored.check_password(PASSWORD)


def test_direct_password_save_updates_stamp(user):
    user.set_password(NEW_PASSWORD)
    user.save()
    user.refresh_from_db()
    assert user.password_changed_at is not None
