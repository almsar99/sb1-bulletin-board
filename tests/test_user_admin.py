"""Сохранение пользователей через админку при параллельном изменении учётной записи."""

import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests.conftest import PASSWORD
from users.admin import UserAdminPasswordChangeForm
from users.models import User, UserRole

pytestmark = pytest.mark.django_db
NEW_PASSWORD = "Changed-Strong-Passphrase-2026!"


@pytest.fixture
def site_client(client):
    operator = User.objects.create_superuser(email="operator@example.com", password=PASSWORD)
    client.force_login(operator)
    return client


def change_data(user, **changes):
    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "role": user.role,
        "is_active": "on",
        "_save": "Сохранить",
        **changes,
    }


@pytest.mark.parametrize("edit_profile", [False, True])
def test_admin_profile_save_preserves_concurrent_password_and_security_changes(
    site_client, user, monkeypatch, edit_profile
):
    model_admin = admin.site._registry[User]
    original_save = model_admin.save_model
    concurrent = {}

    def save_after_concurrent_change(request, obj, form, change):
        fresh = User.objects.get(pk=obj.pk)
        fresh.set_password(NEW_PASSWORD)
        fresh.role = UserRole.ADMIN
        fresh.is_active = False
        fresh.is_staff = True
        fresh.is_superuser = True
        fresh.email = "changed-concurrently@example.com"
        fresh.last_login = timezone.now()
        fresh.save(update_fields=["password", "role", "is_active", "is_staff", "is_superuser", "email", "last_login"])
        concurrent.update(
            password=fresh.password, stamp=fresh.password_changed_at, last_login=fresh.last_login, email=fresh.email
        )
        assert "password" not in form.changed_data
        return original_save(request, obj, form, change)

    monkeypatch.setattr(model_admin, "save_model", save_after_concurrent_change)
    name = "Обновлённое имя" if edit_profile else user.first_name
    response = site_client.post(
        reverse("admin:users_user_change", args=[user.pk]),
        change_data(user, first_name=name, password="forged-read-only-password"),
    )

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.first_name == name
    assert user.password == concurrent["password"]
    assert user.password_changed_at == concurrent["stamp"]
    assert user.last_login == concurrent["last_login"]
    assert user.email == concurrent["email"]
    assert user.check_password(NEW_PASSWORD)
    assert user.role == UserRole.ADMIN
    assert user.is_active is False
    assert user.is_staff is True
    assert user.is_superuser is True


@pytest.mark.parametrize("disable", [False, True])
def test_admin_password_save_preserves_concurrent_fields_and_revokes_tokens(
    site_client, api_client, user, monkeypatch, disable
):
    response = api_client.post("/api/users/token/", {"email": user.email, "password": PASSWORD})
    assert response.status_code == 200
    tokens = response.json()
    original_save = UserAdminPasswordChangeForm.save

    def save_after_concurrent_change(form, commit=True):
        fresh = User.objects.get(pk=form.user.pk)
        fresh.first_name = "Параллельное изменение"
        fresh.role = UserRole.ADMIN
        fresh.is_active = False
        fresh.is_staff = True
        fresh.is_superuser = True
        fresh.save(update_fields=["first_name", "role", "is_active", "is_staff", "is_superuser"])
        return original_save(form, commit=commit)

    monkeypatch.setattr(UserAdminPasswordChangeForm, "save", save_after_concurrent_change)
    data = (
        {"usable_password": "false", "unset-password": "Отключить"}
        if disable
        else {"usable_password": "true", "password1": NEW_PASSWORD, "password2": NEW_PASSWORD}
    )
    response = site_client.post(reverse("admin:auth_user_password_change", args=[user.pk]), data)

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.has_usable_password() is not disable
    if not disable:
        assert user.check_password(NEW_PASSWORD)
    assert user.password_changed_at is not None
    assert user.first_name == "Параллельное изменение"
    assert user.role == UserRole.ADMIN
    assert user.is_active is False
    assert user.is_staff is True
    assert user.is_superuser is True

    # Токены отозваны сменой пароля даже после повторного включения учётной записи.
    User.objects.filter(pk=user.pk).update(is_active=True)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert api_client.get("/api/users/me/").status_code == 401
    api_client.credentials()
    assert api_client.post("/api/users/token/refresh/", {"refresh": tokens["refresh"]}).status_code == 401
    assert api_client.post("/api/users/token/verify/", {"token": tokens["access"]}).status_code == 401
    assert api_client.post("/api/users/token/verify/", {"token": tokens["refresh"]}).status_code == 401


def test_admin_can_create_user(site_client):
    response = site_client.post(
        reverse("admin:users_user_add"),
        {
            "email": "created@example.com",
            "password1": PASSWORD,
            "password2": PASSWORD,
            "usable_password": "true",
            "role": UserRole.ADMIN,
            "is_staff": "on",
            "_save": "Сохранить",
        },
    )

    assert response.status_code == 302
    created = User.objects.get(email="created@example.com")
    assert created.check_password(PASSWORD)
    assert created.role == UserRole.ADMIN
    assert created.is_staff is True


def test_admin_can_change_roles_permissions_and_groups(site_client, user):
    group = Group.objects.create(name="Редакторы")
    data = change_data(user, role=UserRole.ADMIN, is_staff="on", is_superuser="on", groups=[group.pk])
    data.pop("is_active")

    response = site_client.post(reverse("admin:users_user_change", args=[user.pk]), data)

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.role == UserRole.ADMIN
    assert user.is_active is False
    assert user.is_staff is True
    assert user.is_superuser is True
    assert list(user.groups.all()) == [group]
    assert user.check_password(PASSWORD)


@pytest.mark.parametrize("action", ["add", "change"])
def test_admin_normalizes_email(site_client, api_client, user, action):
    submitted_email = "  AdminCreated@Example.COM  "
    if action == "add":
        response = site_client.post(
            reverse("admin:users_user_add"),
            {
                "email": submitted_email,
                "password1": PASSWORD,
                "password2": PASSWORD,
                "usable_password": "true",
                "role": UserRole.USER,
                "_save": "Сохранить",
            },
        )
    else:
        response = site_client.post(
            reverse("admin:users_user_change", args=[user.pk]), change_data(user, email=submitted_email)
        )
    assert response.status_code == 302
    account = User.objects.get(email="admincreated@example.com")
    if action == "change":
        assert account.pk == user.pk
    assert account.check_password(PASSWORD)

    login = api_client.post("/api/users/token/", {"email": submitted_email, "password": PASSWORD})
    assert login.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    assert api_client.get("/api/users/me/").data["id"] == account.pk
    browser = Client()
    login = browser.post("/login/", {"username": submitted_email, "password": PASSWORD})
    assert login.status_code == 302
    assert browser.session["_auth_user_id"] == str(account.pk)


@pytest.mark.parametrize("action", ["add", "change"])
def test_admin_rejects_duplicate_email(site_client, user, other_user, action):
    legacy_email = "Occupied@Example.COM"
    # QuerySet.update воспроизводит старые записи до введения нормализации.
    User.objects.filter(pk=other_user.pk).update(email=legacy_email)
    other_user.refresh_from_db()
    initial_count = User.objects.count()
    submitted_email = "  OCCUPIED@EXAMPLE.COM  "
    if action == "add":
        response = site_client.post(
            reverse("admin:users_user_add"),
            {
                "email": submitted_email,
                "password1": PASSWORD,
                "password2": PASSWORD,
                "usable_password": "true",
                "role": UserRole.USER,
                "_save": "Сохранить",
            },
        )
    else:
        response = site_client.post(
            reverse("admin:users_user_change", args=[user.pk]), change_data(user, email=submitted_email)
        )
    assert response.status_code == 200
    assert "email" in response.context["adminform"].form.errors
    assert User.objects.count() == initial_count
    user.refresh_from_db()
    other_user.refresh_from_db()
    assert user.email == "user@example.com"
    assert other_user.email == legacy_email
    assert user.check_password(PASSWORD) and other_user.check_password(PASSWORD)


def test_legacy_mixed_case_email_can_log_in_without_rewriting_account(api_client, user):
    legacy_email = "Legacy@Example.COM"
    User.objects.filter(pk=user.pk).update(email=legacy_email)
    response = api_client.post("/api/users/token/", {"email": "LEGACY@EXAMPLE.COM", "password": PASSWORD})
    assert response.status_code == 200
    browser = Client()
    response = browser.post("/login/", {"username": "legacy@example.com", "password": PASSWORD})
    assert response.status_code == 302
    assert browser.session["_auth_user_id"] == str(user.pk)
    user.refresh_from_db()
    assert user.email == legacy_email


def test_ambiguous_legacy_email_fails_login_without_choosing_account(api_client, user, other_user):
    User.objects.filter(pk=user.pk).update(email="Duplicate@Example.COM")
    User.objects.filter(pk=other_user.pk).update(email="duplicate@example.com")
    with pytest.raises(User.DoesNotExist):
        User.objects.get_by_natural_key("duplicate@example.com")
    response = api_client.post("/api/users/token/", {"email": "DUPLICATE@EXAMPLE.COM", "password": PASSWORD})
    assert response.status_code == 401
    assert str(response.data["detail"]) == "Неверная почта или пароль."
    browser = Client()
    response = browser.post("/login/", {"username": "duplicate@example.com", "password": PASSWORD})
    assert response.status_code == 200
    assert "_auth_user_id" not in browser.session
    assert User.objects.filter(email__iexact="duplicate@example.com").count() == 2


def test_regular_model_and_manager_saves_normalize_email(user):
    account = User.objects.create_user(email="  Manager@Example.COM  ", password=PASSWORD)
    assert account.email == "manager@example.com"
    account = User(email="  Model@Example.COM  ")
    account.set_password(PASSWORD)
    account.save()
    account.refresh_from_db()
    assert account.email == "model@example.com"
    user.email = "  Changed@Example.COM  "
    user.save(update_fields=["email"])
    user.refresh_from_db()
    assert user.email == "changed@example.com"


def test_model_rejects_legacy_email_collision_with_field_error(user, other_user):
    User.objects.filter(pk=other_user.pk).update(email="Occupied@Example.COM")
    user.email = "  OCCUPIED@EXAMPLE.COM  "
    with pytest.raises(ValidationError) as validation:
        user.validate_unique()
    assert set(validation.value.message_dict) == {"email"}
    with pytest.raises(ValidationError) as save:
        user.save(update_fields=["email"])
    assert set(save.value.message_dict) == {"email"}
    user.refresh_from_db()
    assert user.email == "user@example.com"


@pytest.mark.parametrize("update_fields", [[], ["first_name"]])
def test_unrelated_model_save_does_not_normalize_or_overwrite_email(user, update_fields):
    user.email = "  Stale@Example.COM  "
    user.first_name = "Новое имя"
    User.objects.filter(pk=user.pk).update(email="Current@Example.COM")
    user.save(update_fields=update_fields)
    assert user.email == "  Stale@Example.COM  "
    fresh = User.objects.get(pk=user.pk)
    assert fresh.email == "Current@Example.COM"
    assert fresh.first_name == ("Новое имя" if update_fields else "Иван")
