"""Регистрация, вход по токенам, профиль и права."""

from unittest.mock import patch

import pytest
from django.urls import reverse

from tests.conftest import PASSWORD
from users.models import SignupRequest, User, UserRole
from users.serializers import UserSerializer


@pytest.mark.django_db
class TestRegistration:
    def test_rejects_duplicate_email(self, api_client, user):
        response = api_client.post(
            reverse("users:register"),
            {"email": user.email, "password": PASSWORD, "password_confirm": PASSWORD},
            format="json",
        )

        assert response.status_code == 400
        assert "email" in response.data
        assert not SignupRequest.objects.exists()

    def test_rejects_password_mismatch(self, api_client):
        response = api_client.post(
            reverse("users:register"),
            {
                "email": "mismatch@example.com",
                "password": PASSWORD,
                "password_confirm": "Other-Passphrase-2026",
            },
            format="json",
        )

        assert response.status_code == 400
        assert "password_confirm" in response.data
        assert not SignupRequest.objects.exists()

    def test_rejects_weak_password(self, api_client):
        response = api_client.post(
            reverse("users:register"),
            {"email": "weak@example.com", "password": "12345678", "password_confirm": "12345678"},
            format="json",
        )

        assert response.status_code == 400
        assert "password" in response.data
        assert not SignupRequest.objects.exists()

    def test_requires_email(self, api_client):
        response = api_client.post(
            reverse("users:register"),
            {"password": PASSWORD, "password_confirm": PASSWORD},
            format="json",
        )

        assert response.status_code == 400
        assert not SignupRequest.objects.exists()


@pytest.mark.django_db
class TestTokens:
    def test_login_returns_tokens(self, api_client, user):
        response = api_client.post(
            reverse("users:token-obtain"),
            {"email": user.email.upper(), "password": PASSWORD},
            format="json",
        )

        assert response.status_code == 200
        assert {"access", "refresh"} <= response.data.keys()
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        profile = api_client.get(reverse("users:profile"))
        assert profile.status_code == 200
        assert profile.data["id"] == user.pk
        assert profile.data["email"] == user.email

    def test_refreshes_access(self, api_client, user):
        pair = api_client.post(
            reverse("users:token-obtain"),
            {"email": user.email, "password": PASSWORD},
            format="json",
        ).data

        response = api_client.post(reverse("users:token-refresh"), {"refresh": pair["refresh"]}, format="json")

        assert response.status_code == 200
        assert "access" in response.data


@pytest.mark.django_db
class TestProfile:
    def test_requires_authentication(self, api_client):
        response = api_client.get(reverse("users:profile"))

        assert response.status_code == 401

    def test_returns_own_data(self, user_client, user):
        response = user_client.get(reverse("users:profile"))

        assert response.status_code == 200
        assert response.data["email"] == user.email
        assert response.data["role"] == UserRole.USER

    def test_updates_own_data(self, user_client, user):
        response = user_client.patch(reverse("users:profile"), {"first_name": "Обновлённое"}, format="json")

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.first_name == "Обновлённое"

    def test_role_is_read_only(self, user_client, user):
        user_client.patch(reverse("users:profile"), {"role": UserRole.ADMIN}, format="json")

        user.refresh_from_db()
        assert user.role == UserRole.USER


@pytest.mark.django_db
@pytest.mark.parametrize(
    "method,payload",
    [("patch", {"first_name": "Новое имя"}), ("put", {}), ("patch", {"role": UserRole.ADMIN})],
)
def test_profile_preserves_security(api_client, admin, method, payload):
    admin.is_superuser = True
    admin.save(update_fields=["is_superuser"])
    response = api_client.post(
        reverse("users:token-obtain"), {"email": admin.email, "password": PASSWORD}, format="json"
    )
    assert response.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    original_update = UserSerializer.update

    def revoke_before_profile_save(serializer, instance, validated_data):
        # Администратор меняет права после аутентификации уже начатого запроса.
        User.objects.filter(pk=admin.pk).update(
            role=UserRole.USER,
            is_active=False,
            is_staff=False,
            is_superuser=False,
            email="changed@example.com",
            last_name="Актуальная фамилия",
        )
        return original_update(serializer, instance, validated_data)

    with patch.object(UserSerializer, "update", revoke_before_profile_save):
        response = getattr(api_client, method)(reverse("users:profile"), payload, format="json")

    assert response.status_code == 200
    admin.refresh_from_db()
    assert admin.role == UserRole.USER
    assert not admin.is_active and not admin.is_staff and not admin.is_superuser
    assert admin.email == response.data["email"] == "changed@example.com"
    assert admin.last_name == response.data["last_name"] == "Актуальная фамилия"
    assert response.data["role"] == UserRole.USER
    assert admin.first_name == payload.get("first_name", "")
    assert api_client.get(reverse("users:profile")).status_code == 401


@pytest.mark.django_db
class TestPasswordChange:
    def test_rejects_wrong_current_password(self, user_client, user):
        response = user_client.post(
            reverse("users:password-change"),
            {"current_password": "wrong-password", "new_password": "Another-Str0ng-2026"},
            format="json",
        )

        assert response.status_code == 400
        user.refresh_from_db()
        assert user.check_password(PASSWORD)


@pytest.mark.django_db
class TestModel:
    def test_admin_flag(self, user, admin):
        assert user.is_admin is False
        assert admin.is_admin is True

    def test_string_representation(self, user):
        assert str(user) == user.email

    def test_full_name(self, user, other_user):
        assert user.get_full_name() == "Иван Петров"
        assert other_user.get_full_name() == other_user.email

    def test_requires_email(self, db):
        with pytest.raises(ValueError):
            User.objects.create_user(email="", password=PASSWORD)

    def test_creates_superuser(self, db):
        account = User.objects.create_superuser(email="root@example.com", password=PASSWORD)

        assert account.is_staff is True
        assert account.is_superuser is True
        assert account.is_admin is True


@pytest.mark.django_db
def test_login_error_is_generic(api_client, user):
    responses = [
        api_client.post("/api/users/token/", {"email": email, "password": "wrong"})
        for email in (user.email, "missing@example.com")
    ]
    user.is_active = False
    user.save()
    responses.append(api_client.post("/api/users/token/", {"email": user.email, "password": PASSWORD}))
    assert all(response.status_code == 401 for response in responses)
    assert all(response.data == responses[0].data for response in responses)
    assert str(responses[0].data["detail"]) == "Неверная почта или пароль."
    user.refresh_from_db()
    assert user.last_login is None
