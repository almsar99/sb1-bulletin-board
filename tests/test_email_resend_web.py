"""Пауза между письмами на сайте, в API и после обновления страницы."""

from unittest.mock import patch

import pytest
from django.test import Client

from tests.conftest import PASSWORD
from users.models import SignupRequest

pytestmark = pytest.mark.django_db
START = 1_800_000_000.25


def test_signup_timer_starts_with_first_email_and_survives_refresh(client, mailoutbox):
    data = {"email": "pending@example.com", "password": PASSWORD, "password_confirm": PASSWORD}
    with patch("users.rate_limit.time.time", return_value=START):
        response = client.post("/signup/", data, follow=True)
    signup = SignupRequest.objects.get()
    assert response.context["email_retry_after"] == 60
    assert "Запросить повторно" in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert len(mailoutbox) == 1

    with patch("users.rate_limit.time.time", return_value=START + 15):
        refreshed = client.get("/signup/check-email/")
        repeated = client.post("/signup/resend-verification/", follow=True)
        duplicate_form = client.post("/signup/", data, follow=True)
    assert refreshed.context["email_retry_after"] == 45
    assert repeated.context["email_retry_after"] == 45
    assert duplicate_form.context["email_retry_after"] == 45
    assert len(mailoutbox) == 1
    assert SignupRequest.objects.get().token == signup.token

    with patch("users.rate_limit.time.time", return_value=START + 60):
        available = client.get("/signup/check-email/")
        sent = client.post("/signup/resend-verification/", follow=True)
    assert available.context["email_retry_after"] == 0
    assert sent.context["email_retry_after"] == 60
    assert len(mailoutbox) == 2
    assert SignupRequest.objects.get().token == signup.token


@pytest.mark.parametrize("first_channel", ["web", "api"])
def test_reset_requests_share_cooldown_and_resend_uses_session(client, api_client, user, mailoutbox, first_channel):
    with patch("users.rate_limit.time.time", return_value=START):
        if first_channel == "api":
            assert api_client.post("/api/users/reset_password/", {"email": user.email.upper()}).status_code == 204
        response = client.post("/password-reset/", {"email": f" {user.email.upper()} "}, follow=True)
        assert api_client.post("/users/reset_password/", {"email": user.email}).status_code == 204
        repeated = client.post("/password-reset/resend/", {"email": "another@example.com"}, follow=True)
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [user.email]
    assert response.context["email_retry_after"] == repeated.context["email_retry_after"] == 60
    assert "Запросить повторно" in response.content.decode()
    assert user.email not in response.content.decode()
    assert "no-store" in response["Cache-Control"]

    with patch("users.rate_limit.time.time", return_value=START + 35):
        refreshed = client.get("/password-reset/done/")
        blocked = client.post("/password-reset/resend/", follow=True)
    assert refreshed.context["email_retry_after"] == blocked.context["email_retry_after"] == 25
    assert len(mailoutbox) == 1

    with patch("users.rate_limit.time.time", return_value=START + 60):
        available = client.get("/password-reset/done/")
        sent = client.post("/password-reset/resend/", {"email": "another@example.com"}, follow=True)
    assert available.context["email_retry_after"] == 0
    assert sent.context["email_retry_after"] == 60
    assert len(mailoutbox) == 2
    assert mailoutbox[1].to == [user.email]


@pytest.mark.parametrize("known", [False, True])
def test_reset_confirmation_and_cooldown_do_not_disclose_account(client, user, mailoutbox, known):
    email = user.email if known else "unknown@example.com"
    with patch("users.rate_limit.time.time", return_value=START):
        response = client.post("/password-reset/", {"email": email}, follow=True)
    assert response.status_code == 200
    assert response.context["email_retry_after"] == 60
    assert "Если аккаунт с таким адресом существует, мы отправили письмо со ссылкой." in response.content.decode()
    assert email not in response.content.decode()
    with patch("users.rate_limit.time.time", return_value=START + 20):
        repeated = client.post("/password-reset/resend/", follow=True)
    assert repeated.context["email_retry_after"] == 40
    assert len(mailoutbox) == int(known)


def test_reset_resend_requires_a_previous_request_in_the_session(client, user, mailoutbox):
    response = client.post("/password-reset/resend/", {"email": user.email})
    assert response.status_code == 302 and response.url == "/password-reset/"
    assert not mailoutbox
    assert client.get("/password-reset/done/").context["email_retry_after"] == 0


def test_reset_resend_requires_post_and_csrf(user, mailoutbox):
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session["reset_email"] = user.email
    session.save()
    assert client.get("/password-reset/resend/").status_code == 405
    response = client.post("/password-reset/resend/")
    assert response.status_code == 403
    assert "Не удалось отправить форму" in response.content.decode()
    assert not mailoutbox


def test_reset_resend_keeps_the_shared_request_budget(client, api_client, user, settings, mailoutbox):
    settings.AUTH_REQUEST_LIMITS = {"reset": {"ip": (100, 60), "identity": (2, 3600)}}
    with patch("users.rate_limit.time.time", return_value=START):
        assert client.post("/password-reset/", {"email": user.email}).status_code == 302
        assert api_client.post("/api/users/reset_password/", {"email": user.email}).status_code == 204
        response = client.post("/password-reset/resend/", {"email": "spoof@example.com"})
    assert response.status_code == 429
    assert "Retry-After" in response
    assert len(mailoutbox) == 1


@pytest.mark.parametrize("purpose", ["signup", "reset"])
def test_email_can_be_requested_every_minute_without_an_hourly_lockout(api_client, user, mailoutbox, purpose):
    if purpose == "signup":
        url = "/api/users/register/"
        data = {"email": "pending@example.com", "password": PASSWORD, "password_confirm": PASSWORD}
        sent_status, repeated_status = 201, 429
    else:
        url = "/api/users/reset_password/"
        data = {"email": user.email}
        sent_status = repeated_status = 204
    for minute in range(6):
        with patch("users.rate_limit.time.time", return_value=START + minute * 60):
            assert api_client.post(url, data).status_code == sent_status
        with patch("users.rate_limit.time.time", return_value=START + minute * 60 + 1):
            for _ in range(3):
                assert api_client.post(url, data).status_code == repeated_status
        assert len(mailoutbox) == minute + 1
