"""Заявки на регистрацию: создание учётной записи только после подтверждения."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import check_password, get_hasher, identify_hasher
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.management import call_command
from django.db import close_old_connections
from django.utils import timezone

from tests.conftest import PASSWORD
from users.models import SignupRequest, User, UserRole
from users.rate_limit import allow_signup_resend, email_send_wait
from users.signup import confirm_signup_request, submit_signup_request

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/users/register/"
LOGIN_URL = "/api/users/token/"
RESEND_URL = "/api/users/signup/resend/"
INVALID_LINK = "Ссылка недействительна или истекла"


def registration_payload(email="new@example.com", **overrides):
    return {
        "email": email,
        "first_name": "Пётр",
        "last_name": "Сидоров",
        "phone": "+79991112233",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
        **overrides,
    }


def confirmation_url(signup):
    return f"/signup/confirm/{signup.token}/"


@pytest.fixture
def signup_request(signup_request_factory):
    return signup_request_factory(
        email="pending@example.com",
        password=PASSWORD,
        first_name="Пётр",
        last_name="Сидоров",
        phone="+79991112233",
    )


@pytest.mark.parametrize("url,expected_status", [(REGISTER_URL, 201), ("/signup/", 302)])
def test_initial_registration_starts_resend_pause_before_delivery(api_client, mailoutbox, url, expected_status):
    def deliver_after_pause(*args, **kwargs):
        assert not allow_signup_resend(args[3][0])
        return send_mail(*args, **kwargs)

    with patch("users.email.send_mail", side_effect=deliver_after_pause) as delivery:
        response = api_client.post(url, registration_payload())

    assert response.status_code == expected_status
    delivery.assert_called_once()
    assert len(mailoutbox) == 1
    assert not User.objects.exists()


def test_signup_creates_request(api_client):
    response = api_client.post(REGISTER_URL, registration_payload("New@Example.COM"), format="json")

    assert response.status_code == 201
    assert response.data["email"] == "new@example.com"
    assert not {"password", "password_confirm", "token", "id", "access", "refresh"} & response.data.keys()
    assert not User.objects.exists()
    signup = SignupRequest.objects.get(email="new@example.com")
    assert signup.first_name == "Пётр"
    assert signup.last_name == "Сидоров"
    assert signup.phone == "+79991112233"
    assert signup.password != PASSWORD
    assert check_password(PASSWORD, signup.password)
    assert identify_hasher(signup.password).algorithm == get_hasher().algorithm
    assert signup.created_at is not None
    assert not signup.is_expired


def test_registration_sends_text_and_html_email_from_configured_origin(api_client, mailoutbox, settings):
    settings.FRONTEND_URL = "https://accounts.example.org/application/"
    response = api_client.post(REGISTER_URL, registration_payload(), format="json", HTTP_HOST="testserver")

    assert response.status_code == 201
    assert len(mailoutbox) == 1
    signup = SignupRequest.objects.get()
    message = mailoutbox[0]
    link = f"https://accounts.example.org/application/signup/confirm/{signup.token}/"
    assert message.to == [signup.email]
    assert message.subject
    assert link in message.body
    assert any(link in content and mimetype == "text/html" for content, mimetype in message.alternatives)
    assert "testserver" not in message.body
    assert PASSWORD not in message.body
    assert signup.password not in message.body


@pytest.mark.parametrize("login_method", ["api", "web"])
def test_request_cannot_login_and_original_link_still_works(api_client, client, mailoutbox, login_method):
    assert api_client.post(REGISTER_URL, registration_payload(), format="json").status_code == 201
    signup = SignupRequest.objects.get()
    url = re.search(r"/signup/confirm/[^/\s]+/", mailoutbox[0].body).group()

    if login_method == "api":
        response = api_client.post(LOGIN_URL, {"email": signup.email, "password": PASSWORD}, format="json")
        missing = api_client.post(LOGIN_URL, {"email": "missing@example.com", "password": PASSWORD}, format="json")
        assert response.status_code == missing.status_code == 401
        assert response.json() == missing.json() == {"detail": "Неверная почта или пароль."}
        assert not {"access", "refresh"} & response.data.keys()
    else:
        response = client.post("/login/", {"username": signup.email, "password": PASSWORD})
        missing = client.post("/login/", {"username": "missing@example.com", "password": PASSWORD})
        assert response.status_code == missing.status_code == 200
        assert response.context["form"].non_field_errors() == missing.context["form"].non_field_errors()
        assert "_auth_user_id" not in client.session

    assert not User.objects.exists()
    signup.refresh_from_db()
    assert confirmation_url(signup) == url
    assert client.get(url).context["verified"] is True
    assert User.objects.filter(email=signup.email).exists()


def test_confirmation_creates_account_with_exact_hash_and_fields(client, signup_request):
    original_hash = signup_request.password
    response = client.get(confirmation_url(signup_request))

    assert response.status_code == 200
    assert response.context["verified"] is True
    assert any(template.name == "accounts/signup_confirmation_result.html" for template in response.templates)
    user = User.objects.get(email=signup_request.email)
    assert (user.first_name, user.last_name, user.phone) == (
        signup_request.first_name,
        signup_request.last_name,
        signup_request.phone,
    )
    assert user.password == original_hash
    assert user.check_password(PASSWORD)
    assert user.email_verified_at is not None
    assert user.role == UserRole.USER
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.last_login is None
    assert not SignupRequest.objects.exists()
    assert "_auth_user_id" not in client.session
    assert 'href="/login/"' in response.content.decode()


@pytest.mark.parametrize("login_method", ["api", "web"])
def test_confirmed_account_can_login(api_client, client, signup_request, login_method):
    assert client.get(confirmation_url(signup_request)).context["verified"] is True
    if login_method == "api":
        response = api_client.post(LOGIN_URL, {"email": signup_request.email, "password": PASSWORD}, format="json")
        assert response.status_code == 200
        assert {"access", "refresh"} <= response.data.keys()
    else:
        response = client.post("/login/", {"username": signup_request.email, "password": PASSWORD})
        assert response.status_code == 302
        assert "_auth_user_id" in client.session
    assert User.objects.get(email=signup_request.email).last_login is not None


def test_signup_link_expires(client, signup_request):
    now = timezone.now()
    SignupRequest.objects.filter(pk=signup_request.pk).update(created_at=now - timedelta(days=1))
    with patch("django.utils.timezone.now", return_value=now):
        response = client.get(confirmation_url(signup_request))

    assert response.status_code == 200
    assert response.context["verified"] is False
    assert INVALID_LINK in response.content.decode()
    assert not User.objects.exists()
    assert SignupRequest.objects.filter(pk=signup_request.pk).exists()


def test_confirmation_works_just_before_expiry(client, signup_request):
    now = timezone.now()
    SignupRequest.objects.filter(pk=signup_request.pk).update(created_at=now - timedelta(days=1, microseconds=-1))
    with patch("django.utils.timezone.now", return_value=now):
        response = client.get(confirmation_url(signup_request))
    assert response.context["verified"] is True
    assert User.objects.count() == 1


@pytest.mark.parametrize("token", ["bad\x00token", "a" * 43])
def test_signup_rejects_bad_token(client, signup_request, token):
    response = client.get(f"/signup/confirm/{token}/")

    assert response.status_code == 200
    assert response.context["verified"] is False
    assert INVALID_LINK in response.content.decode()
    assert not User.objects.exists()
    assert SignupRequest.objects.filter(pk=signup_request.pk).exists()


def test_repeat_registration_replaces_request_and_sends_fresh_email(api_client, client, mailoutbox):
    start = time.time()
    with patch("users.rate_limit.time.time", return_value=start):
        first_response = api_client.post(REGISTER_URL, registration_payload(), format="json")
    first = SignupRequest.objects.get()
    original_created_at = timezone.now() - timedelta(hours=2)
    SignupRequest.objects.filter(pk=first.pk).update(created_at=original_created_at)
    new_password = "New-Str0ng-Passphrase-2026"
    with patch("users.rate_limit.time.time", return_value=start + 60):
        second_response = api_client.post(
            REGISTER_URL,
            registration_payload("NEW@example.com", password=new_password, password_confirm=new_password),
            format="json",
        )

    assert first_response.status_code == second_response.status_code == 201
    assert first_response.data == second_response.data
    second = SignupRequest.objects.get()
    assert second.pk != first.pk
    assert second.token != first.token
    assert second.created_at > original_created_at
    assert check_password(new_password, second.password)
    assert not check_password(PASSWORD, second.password)
    assert len(mailoutbox) == 2
    assert confirmation_url(first) in mailoutbox[0].body
    assert confirmation_url(second) in mailoutbox[1].body
    assert client.get(confirmation_url(first)).context["verified"] is False
    assert not User.objects.exists()
    assert client.get(confirmation_url(second)).context["verified"] is True
    assert User.objects.get().check_password(new_password)


def test_repeat_registration_during_cooldown_keeps_sent_link_and_password(api_client, client, mailoutbox):
    start = time.time()
    with patch("users.rate_limit.time.time", return_value=start):
        assert api_client.post(REGISTER_URL, registration_payload(), format="json").status_code == 201
    first = SignupRequest.objects.get()
    for elapsed, expected_wait in ((0, 60), (30, 30), (59.5, 1)):
        with patch("users.rate_limit.time.time", return_value=start + elapsed):
            response = api_client.post(
                REGISTER_URL,
                registration_payload(
                    "NEW@example.com",
                    password="Another-Str0ng-Passphrase-2026",
                    password_confirm="Another-Str0ng-Passphrase-2026",
                ),
                format="json",
            )
        assert response.status_code == 429
        assert int(response["Retry-After"]) == expected_wait
        current = SignupRequest.objects.get()
        assert (current.pk, current.token, current.password, current.created_at) == (
            first.pk,
            first.token,
            first.password,
            first.created_at,
        )
    assert len(mailoutbox) == 1
    assert client.get(confirmation_url(first)).context["verified"] is True
    assert User.objects.get().check_password(PASSWORD)


def test_resend_then_initial_registration_cannot_bypass_cooldown(api_client, signup_request, mailoutbox):
    assert api_client.post(RESEND_URL, {"email": signup_request.email}).status_code == 204
    response = api_client.post(REGISTER_URL, registration_payload(signup_request.email), format="json")
    assert response.status_code == 429
    assert SignupRequest.objects.get().token == signup_request.token
    assert len(mailoutbox) == 1


def test_invalid_signup_does_not_reserve_delivery_pause():
    with pytest.raises(ValidationError):
        submit_signup_request(email="invalid@example.com", password="weak")
    assert email_send_wait("signup", "invalid@example.com") == 0
    assert not SignupRequest.objects.exists()


def test_registration_rejects_existing_phone(api_client, user):
    response = api_client.post(REGISTER_URL, registration_payload(phone=user.phone), format="json")
    assert response.status_code == 400
    assert "phone" in response.data
    assert not SignupRequest.objects.exists()
    assert User.objects.count() == 1


def test_registration_cannot_set_privileged_account_fields(api_client, client):
    response = api_client.post(
        REGISTER_URL,
        registration_payload(role=UserRole.ADMIN, is_staff=True, is_superuser=True),
        format="json",
    )
    assert response.status_code == 201
    assert client.get(confirmation_url(SignupRequest.objects.get())).context["verified"] is True
    user = User.objects.get()
    assert user.role == UserRole.USER
    assert not user.is_staff and not user.is_superuser


def test_resend_sends_email_for_pending_request(api_client, signup_request, mailoutbox):
    response = api_client.post(RESEND_URL, {"email": signup_request.email.upper()}, format="json")
    assert response.status_code == 204
    assert response.content == b""
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [signup_request.email]
    assert confirmation_url(signup_request) in mailoutbox[0].body


@pytest.mark.parametrize("first_channel", ["api", "web"])
def test_resend_cooldown_shared_between_api_and_web(api_client, client, signup_request, mailoutbox, first_channel):
    session = client.session
    session["verification_email"] = signup_request.email.upper()
    session.save()
    original_state = (signup_request.token, signup_request.created_at)

    def resend(channel):
        if channel == "api":
            response = api_client.post(RESEND_URL, {"email": signup_request.email}, format="json")
            assert response.status_code == 204 and response.content == b""
        else:
            response = client.post("/signup/resend-verification/", {"email": "ignored@example.com"}, follow=True)
            assert response.redirect_chain == [("/signup/check-email/", 302)]
            assert 1 <= response.context["email_retry_after"] <= 60

    second_channel = "web" if first_channel == "api" else "api"
    with patch("django.core.cache.backends.locmem.time.time", return_value=1000000) as clock:
        resend(first_channel)
        resend(second_channel)
        assert len(mailoutbox) == 1
        clock.return_value += 59
        resend(second_channel)
        assert len(mailoutbox) == 1
        clock.return_value += 1
        resend(second_channel)
        assert len(mailoutbox) == 2

    assert all(message.to == [signup_request.email] for message in mailoutbox)
    assert mailoutbox[0].body == mailoutbox[1].body
    signup_request.refresh_from_db()
    assert (signup_request.token, signup_request.created_at) == original_state
    assert not User.objects.exists()


def test_resend_does_not_change_token_or_extend_expiration(api_client, client, signup_request, mailoutbox):
    now = timezone.now()
    created_at = now - timedelta(hours=23, minutes=59)
    SignupRequest.objects.filter(pk=signup_request.pk).update(created_at=created_at)
    original_token = signup_request.token
    assert api_client.post(RESEND_URL, {"email": signup_request.email}, format="json").status_code == 204
    assert len(mailoutbox) == 1
    signup_request.refresh_from_db()
    assert signup_request.created_at == created_at
    assert signup_request.token == original_token
    with patch("django.utils.timezone.now", return_value=created_at + timedelta(days=1)):
        assert client.get(confirmation_url(signup_request)).context["verified"] is False
    assert not User.objects.exists()


def test_resend_response_same_for_pending_missing_confirmed_and_expired(
    api_client, signup_request, user, mailoutbox, signup_request_factory
):
    expired = signup_request_factory(email="expired@example.com", password=PASSWORD)
    SignupRequest.objects.filter(pk=expired.pk).update(created_at=timezone.now() - timedelta(days=2))
    responses = [
        api_client.post(RESEND_URL, {"email": email}, format="json")
        for email in (signup_request.email, signup_request.email, "missing@example.com", user.email, expired.email)
    ]
    assert all(response.status_code == 204 and response.content == b"" for response in responses)
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [signup_request.email]
    assert SignupRequest.objects.filter(pk=expired.pk).exists()


def test_purge_signup_requests_deletes_only_expired(signup_request, signup_request_factory):
    expired = signup_request_factory(email="expired@example.com", password=PASSWORD)
    older = signup_request_factory(email="older@example.com", password=PASSWORD)
    SignupRequest.objects.filter(pk__in=[expired.pk, older.pk]).update(created_at=timezone.now() - timedelta(days=2))
    stdout = StringIO()
    call_command("purge_signup_requests", stdout=stdout)
    assert list(SignupRequest.objects.values_list("pk", flat=True)) == [signup_request.pk]
    assert "2" in stdout.getvalue()
    assert not User.objects.exists()


def test_email_failure_preserves_request_and_logs_no_secrets(api_client, caplog):
    sensitive_error = f"SMTP rejected new@example.com {PASSWORD} https://secret.example/confirmation-token"
    with patch("users.email.send_mail", side_effect=RuntimeError(sensitive_error)):
        response = api_client.post(REGISTER_URL, registration_payload(), format="json")

    assert response.status_code == 201
    signup = SignupRequest.objects.get()
    assert email_send_wait("signup", signup.email) > 0
    assert not User.objects.exists()
    records = [record for record in caplog.records if record.name == "users.email"]
    assert records
    assert any(str(signup.pk) in record.getMessage() for record in records)
    for record in records:
        assert not record.exc_info
    for secret in (signup.email, PASSWORD, signup.password, signup.token, sensitive_error):
        assert secret not in caplog.text


@pytest.mark.parametrize("login_method", ["api", "web"])
def test_existing_account_without_confirmation_timestamp_can_login(api_client, client, login_method):
    user = User.objects.create_user(email="legacy@example.com", password=PASSWORD)
    assert user.email_verified_at is None
    if login_method == "api":
        response = api_client.post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
        assert response.status_code == 200
        assert {"access", "refresh"} <= response.data.keys()
    else:
        response = client.post("/login/", {"username": user.email, "password": PASSWORD})
        assert response.status_code == 302
        assert int(client.session["_auth_user_id"]) == user.pk
    user.refresh_from_db()
    assert user.email_verified_at is None
    assert user.last_login is not None


@pytest.mark.parametrize("conflicting_field", ["email", "phone"])
def test_confirmation_rejects_details_claimed_after_request(client, signup_request, conflicting_field):
    existing = User.objects.create_user(
        email=signup_request.email if conflicting_field == "email" else "claimed@example.com",
        phone=signup_request.phone if conflicting_field == "phone" else "",
        password="Different-Str0ng-Password-2026",
    )
    response = client.get(confirmation_url(signup_request))
    assert response.status_code == 200
    assert response.context["verified"] is False
    assert INVALID_LINK in response.content.decode()
    assert User.objects.count() == 1
    existing.refresh_from_db()
    assert not existing.check_password(PASSWORD)


@pytest.mark.parametrize("url", ["/verify-email/old/token/", "/api/users/verify-email/old/token/"])
def test_old_confirmation_endpoints_removed(client, url):
    assert client.get(url).status_code == 404


def test_old_resend_endpoint_removed(api_client):
    assert api_client.post("/api/users/verify-email/resend/", {"email": "pending@example.com"}).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmation_creates_exactly_one_account(signup_request_factory):
    signup = signup_request_factory(email="race@example.com", password=PASSWORD)
    barrier = Barrier(2, timeout=10)

    def confirm():
        close_old_connections()
        try:
            barrier.wait()
            user = confirm_signup_request(signup.token)
            return user.pk if user else None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(confirm) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]

    assert sum(result is not None for result in results) == 1
    assert User.objects.filter(email=signup.email).count() == 1
    assert not SignupRequest.objects.filter(pk=signup.pk).exists()
    assert next(result for result in results if result is not None) == User.objects.get(email=signup.email).pk
