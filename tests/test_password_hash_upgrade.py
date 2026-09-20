"""Обновление старых хешей одновременно со сменой пароля или состояния аккаунта."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.hashers import PBKDF2PasswordHasher, make_password
from django.db import close_old_connections, connection
from django.db.models import QuerySet
from django.test import Client, override_settings
from rest_framework.test import APIClient

from tests.conftest import PASSWORD
from tests.test_token_revocation import NEW_PASSWORD, change_password, issue, profile
from users.models import User

pytestmark = pytest.mark.django_db


class UpgradeAccount(tuple):
    """Не выводить JWT из аргумента-фикстуры в отчёте pytest о падении."""

    def __repr__(self):
        return f"UpgradeAccount(user={self[0]!r}, tokens=<hidden>)"


@pytest.fixture(params=["algorithm", "iterations"])
def outdated_account(request, db, settings, monkeypatch):
    monkeypatch.setattr(PBKDF2PasswordHasher, "iterations", 1000)
    if request.param == "iterations":
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
    user = User.objects.create_superuser(email="upgrade@example.com", password=PASSWORD)
    tokens = issue(APIClient(), user)
    user.refresh_from_db()
    assert user.password.startswith("md5$" if request.param == "algorithm" else "pbkdf2_sha256$1000$")
    monkeypatch.setattr(PBKDF2PasswordHasher, "iterations", 2000)
    settings.PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.PBKDF2PasswordHasher",
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]
    return UpgradeAccount((user, tokens))


def attempt_login(channel, email):
    client = APIClient() if channel == "api" else Client()
    url = {"api": "/api/users/token/", "web": "/login/", "admin": "/admin/login/"}[channel]
    field = "email" if channel == "api" else "username"
    return client, client.post(url, {field: email, "password": PASSWORD})


def assert_login_denied(channel, client, response):
    if channel == "api":
        assert response.status_code == 401
        assert "access" not in response.data
    else:
        assert response.status_code == 200
        assert "_auth_user_id" not in client.session


def assert_reset_preserved(user, tokens, password_state):
    user.refresh_from_db()
    assert user.password == password_state["hash"]
    assert user.password_changed_at == password_state["stamp"]
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(PASSWORD)
    client = APIClient()
    assert profile(client, tokens["access"]).status_code == 401
    assert client.post("/api/users/token/refresh/", {"refresh": tokens["refresh"]}).status_code == 401
    assert client.post("/api/users/token/", {"email": user.email, "password": PASSWORD}).status_code == 401
    fresh = issue(client, user, NEW_PASSWORD)
    assert profile(client, fresh["access"]).status_code == 200


@pytest.mark.parametrize("channel,flow", [("api", "api_reset"), ("web", "web_reset"), ("admin", "change")])
def test_hash_upgrade_preserves_reset(outdated_account, monkeypatch, channel, flow):
    user, tokens = outdated_account
    original_update = QuerySet.update
    password_state = {}

    def change_before_compare_and_swap(queryset, **changes):
        if queryset.model is User and set(changes) == {"password"} and not password_state:
            password_state["started"] = True
            change_password(flow, APIClient(), Client(), user, tokens)
            user.refresh_from_db()
            password_state.update(hash=user.password, stamp=user.password_changed_at)
        return original_update(queryset, **changes)

    monkeypatch.setattr(QuerySet, "update", change_before_compare_and_swap)
    client, response = attempt_login(channel, user.email)
    assert password_state["started"]
    assert_login_denied(channel, client, response)
    assert_reset_preserved(user, tokens, password_state)


@pytest.mark.parametrize("channel", ["api", "web", "admin"])
@pytest.mark.parametrize("account_state", ["inactive", "deleted"])
def test_upgrade_rechecks_account_state_before_login(outdated_account, monkeypatch, channel, account_state):
    user, _ = outdated_account
    original_update = QuerySet.update
    changed = False

    def change_before_compare_and_swap(queryset, **changes):
        nonlocal changed
        if queryset.model is User and set(changes) == {"password"} and not changed:
            changed = True
            accounts = User.objects.filter(pk=user.pk)
            if account_state == "deleted":
                accounts.delete()
            else:
                accounts.update(is_active=False)
        return original_update(queryset, **changes)

    monkeypatch.setattr(QuerySet, "update", change_before_compare_and_swap)
    client, response = attempt_login(channel, user.email)
    assert changed
    assert_login_denied(channel, client, response)
    if account_state == "deleted":
        assert not User.objects.filter(pk=user.pk).exists()
    else:
        user.refresh_from_db()
        assert not user.is_active
        assert user.password_changed_at is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("account_change", ["password", "inactive", "deleted"])
def test_async_upgrade_rechecks_current_password_and_state(outdated_account, monkeypatch, account_change):
    user, tokens = outdated_account
    original_update = QuerySet.update
    changed = False

    def change_before_compare_and_swap(queryset, **changes):
        nonlocal changed
        if queryset.model is User and set(changes) == {"password"} and not changed:
            changed = True
            if account_change == "password":
                change_password("api_reset", APIClient(), Client(), User.objects.get(pk=user.pk), tokens)
            elif account_change == "inactive":
                User.objects.filter(pk=user.pk).update(is_active=False)
            else:
                User.objects.filter(pk=user.pk).delete()
        return original_update(queryset, **changes)

    monkeypatch.setattr(QuerySet, "update", change_before_compare_and_swap)
    assert not async_to_sync(user.acheck_password)(PASSWORD)
    assert changed
    if account_change == "password":
        fresh = User.objects.get(pk=user.pk)
        assert fresh.check_password(NEW_PASSWORD)
        assert not fresh.check_password(PASSWORD)
    elif account_change == "inactive":
        assert not user.is_active
    else:
        assert not User.objects.filter(pk=user.pk).exists()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_another_upgrade_of_same_password_can_still_authenticate(outdated_account, monkeypatch, asynchronous):
    user, tokens = outdated_account
    original_update = QuerySet.update
    competing_hash = make_password(PASSWORD)
    changed = False

    def replace_before_compare_and_swap(queryset, **changes):
        nonlocal changed
        if queryset.model is User and set(changes) == {"password"} and not changed:
            changed = True
            original_update(User.objects.filter(pk=user.pk), password=competing_hash)
        return original_update(queryset, **changes)

    monkeypatch.setattr(QuerySet, "update", replace_before_compare_and_swap)
    check = async_to_sync(user.acheck_password) if asynchronous else user.check_password
    assert check(PASSWORD)
    assert changed and user.password == competing_hash
    assert user.password_changed_at is None
    assert profile(APIClient(), tokens["access"]).status_code == 200


@pytest.mark.parametrize("asynchronous", [False, True])
def test_unsaved_account_can_check_and_upgrade_hash_without_creating_user(settings, asynchronous):
    settings.PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.PBKDF2PasswordHasher",
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]
    user = User(email="unsaved@example.com", password=make_password(PASSWORD, hasher="md5"))
    check = async_to_sync(user.acheck_password) if asynchronous else user.check_password
    assert not check("wrong")
    assert check(PASSWORD)
    assert user.pk is None and user._state.adding
    assert user.password.startswith("pbkdf2_sha256$")
    assert not User.objects.filter(email=user.email).exists()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_normal_upgrade_preserves_jwt_timestamp_and_other_fields(outdated_account, asynchronous):
    user, tokens = outdated_account
    original_email = user.email
    User.objects.filter(pk=user.pk).update(first_name="Актуальное имя", role="user")
    check = async_to_sync(user.acheck_password) if asynchronous else user.check_password
    assert check(PASSWORD)
    user.refresh_from_db()
    assert user.password.startswith("pbkdf2_sha256$2000$")
    assert user.password_changed_at is None
    assert user.email == original_email and user.first_name == "Актуальное имя" and user.role == "user"
    client = APIClient()
    assert profile(client, tokens["access"]).status_code == 200
    assert client.post("/api/users/token/refresh/", {"refresh": tokens["refresh"]}).status_code == 200


@pytest.mark.parametrize("asynchronous", [False, True])
def test_check_preserves_pending_password_until_explicit_save(outdated_account, asynchronous):
    user, tokens = outdated_account
    original_hash = user.password
    with override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]):
        user.set_password(NEW_PASSWORD)
    check = async_to_sync(user.acheck_password) if asynchronous else user.check_password
    assert check(NEW_PASSWORD)
    assert user._password == NEW_PASSWORD
    assert user.password.startswith("pbkdf2_sha256$2000$")
    stored = User.objects.get(pk=user.pk)
    assert stored.password == original_hash and stored.password_changed_at is None
    user.save(update_fields=["password"])
    user.refresh_from_db()
    assert user.password_changed_at is not None
    assert user.check_password(NEW_PASSWORD)
    assert profile(APIClient(), tokens["access"]).status_code == 401


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("flow", ["api_reset", "change"])
def test_two_postgres_connections_cannot_restore_old_password(outdated_account, monkeypatch, flow):
    user, tokens = outdated_account
    assert connection.vendor == "postgresql"
    upgrade_ready = Event()
    password_changed = Event()
    original_encode = PBKDF2PasswordHasher.encode
    context = {}
    password_state = {}

    def pause_after_hash_calculation(hasher, password, salt, iterations=None):
        encoded = original_encode(hasher, password, salt, iterations=iterations)
        # Обе реализации (штатный setter Django и CAS) вычисляют новый хеш
        # до записи. Старую PBKDF2-проверку с явными iterations не останавливаем.
        if iterations is None and get_ident() == context.get("login_thread"):
            context["login_pid"] = connection.connection.info.backend_pid
            upgrade_ready.set()
            assert password_changed.wait(timeout=15)
        return encoded

    def login_worker():
        close_old_connections()
        context["login_thread"] = get_ident()
        try:
            _, response = attempt_login("api", user.email)
            return response.status_code
        finally:
            close_old_connections()

    def password_worker():
        close_old_connections()
        try:
            assert upgrade_ready.wait(timeout=15)
            fresh = User.objects.get(pk=user.pk)
            context["password_pid"] = connection.connection.info.backend_pid
            change_password(flow, APIClient(), Client(), fresh, tokens)
            fresh.refresh_from_db()
            password_state.update(hash=fresh.password, stamp=fresh.password_changed_at)
        finally:
            password_changed.set()
            close_old_connections()

    monkeypatch.setattr(PBKDF2PasswordHasher, "encode", pause_after_hash_calculation)
    with ThreadPoolExecutor(max_workers=2) as executor:
        login_result = executor.submit(login_worker)
        password_result = executor.submit(password_worker)
        password_result.result(timeout=20)
        assert login_result.result(timeout=20) == 401
    assert context["login_pid"] != context["password_pid"]
    assert_reset_preserved(user, tokens, password_state)
