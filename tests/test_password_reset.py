"""Восстановление пароля, срок действия и однократность ссылки."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.db import connections
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from tests.conftest import PASSWORD
from users.email import send_password_reset_email
from users.models import User
from users.serializers import PasswordResetConfirmSerializer
from users.tokens import make_reset_credentials

pytestmark = pytest.mark.django_db
REQUEST_URL = "/api/users/reset_password/"
CONFIRM_URL = "/api/users/reset_password_confirm/"
NEW_PASSWORD = "New-Str0ng-Passphrase-2026"


def payload(user):
    uid, token = make_reset_credentials(user)
    return {"uid": uid, "token": token, "new_password": NEW_PASSWORD}


@pytest.mark.parametrize("url", [REQUEST_URL, "/users/reset_password/"])
def test_request_sends_link(api_client, user, mailoutbox, url):
    assert api_client.post(url, {"email": user.email.upper()}).status_code == 204
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [user.email]
    match = re.search(r"http://testserver/password-reset/([^/]+)/([^/]+)/", mailoutbox[0].body)
    assert match
    assert match.group(1) == urlsafe_base64_encode(force_bytes(user.pk))
    assert default_token_generator.check_token(user, match.group(2))


def test_unknown_email_same_response(api_client, user, mailoutbox):
    unknown = api_client.post(REQUEST_URL, {"email": "missing@example.com"})
    assert not mailoutbox
    known = api_client.post(REQUEST_URL, {"email": user.email})
    assert unknown.status_code == known.status_code == 204
    assert unknown.content == known.content == b""


@pytest.mark.parametrize("url", [CONFIRM_URL, "/users/reset_password_confirm/", "/users/reset_password_confirm"])
def test_confirmation_changes_password(api_client, user, url):
    assert api_client.post(url, payload(user)).status_code == 204
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(PASSWORD)
    assert api_client.post("/api/users/token/", {"email": user.email, "password": PASSWORD}).status_code == 401
    assert api_client.post("/api/users/token/", {"email": user.email, "password": NEW_PASSWORD}).status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("url", [CONFIRM_URL, "/users/reset_password_confirm/", "/users/reset_password_confirm"])
def test_deleted_account_between_lookup_and_lock_is_invalid_link(api_client, user, mailoutbox, url):
    data = payload(user)
    account_id = user.pk
    select_for_update = User.objects.select_for_update

    def delete_account():
        try:
            User.objects.filter(pk=account_id).delete()
        finally:
            connections.close_all()

    def delete_before_lock(*args, **kwargs):
        # Отдельное соединение фиксирует удаление: откат reset не вернёт аккаунт.
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(delete_account).result(timeout=10)
        return select_for_update(*args, **kwargs)

    with patch.object(User.objects, "select_for_update", side_effect=delete_before_lock) as lock_lookup:
        response = api_client.post(url, data, format="json")

    assert lock_lookup.call_count == 1
    assert response.status_code == 400
    assert response.data == ["Ссылка недействительна или истекла"]
    assert not User.objects.filter(pk=account_id).exists()
    assert not mailoutbox


def test_repeated_link_rejected(api_client, user):
    data = payload(user)
    assert api_client.post(CONFIRM_URL, data).status_code == 204
    assert api_client.post(CONFIRM_URL, data).status_code == 400


def test_expired_link_rejected(api_client, user):
    data = payload(user)
    future = default_token_generator._now() + timedelta(days=1, seconds=1)
    with patch.object(default_token_generator, "_now", return_value=future):
        assert api_client.post(CONFIRM_URL, data).status_code == 400


@pytest.mark.parametrize("uid", ["bad!", "", "////", urlsafe_base64_encode(force_bytes(2**80)), "MA"])
def test_invalid_uid_rejected(api_client, user, uid):
    data = payload(user)
    data["uid"] = uid
    assert api_client.post(CONFIRM_URL, data).status_code == 400


def test_forged_token_rejected(api_client, user):
    data = payload(user)
    data["token"] += "x"
    assert api_client.post(CONFIRM_URL, data).status_code == 400


def test_weak_password_rejected(api_client, user):
    data = payload(user)
    data["new_password"] = "12345678"
    assert api_client.post(CONFIRM_URL, data).status_code == 400
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.parametrize("state", ["inactive", "unusable"])
def test_ineligible_account_gets_no_mail(api_client, user, mailoutbox, state):
    data = payload(user)
    if state == "inactive":
        user.is_active = False
    else:
        user.set_unusable_password()
    user.save()
    assert api_client.post(REQUEST_URL, {"email": user.email}).status_code == 204
    assert not mailoutbox
    assert api_client.post(CONFIRM_URL, data).status_code == 400


def test_empty_email_skips_delivery(user, mailoutbox):
    user.email = ""
    send_password_reset_email(user)
    assert not mailoutbox


def test_two_validated_requests_cannot_reuse_token(user):
    data = payload(user)
    first = PasswordResetConfirmSerializer(data=data)
    second = PasswordResetConfirmSerializer(data=data)
    assert first.is_valid() and second.is_valid()
    first.save()
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError):
        second.save()


def test_reset_without_valid_authentication_header(api_client, user):
    api_client.credentials(HTTP_AUTHORIZATION="Bearer expired")
    assert api_client.post(REQUEST_URL, {"email": user.email}).status_code == 204
