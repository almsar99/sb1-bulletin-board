"""Общие ограничения запросов сайта и API, повторных писем и нескольких процессов."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import override_settings

from tests.conftest import PASSWORD
from users.models import SignupRequest
from users.rate_limit import request_limit_wait

pytestmark = pytest.mark.django_db


@pytest.fixture
def limits(settings):
    settings.AUTH_REQUEST_LIMITS = {
        "login": {"ip": (100, 60), "identity": (100, 60)},
        "signup": {"ip": (100, 60), "identity": (100, 3600)},
        "reset": {"ip": (100, 60), "identity": (100, 3600)},
        "confirm": {"ip": (100, 60)},
    }
    settings.SECURE_PROXY_SSL_HEADER = None
    return settings.AUTH_REQUEST_LIMITS


def signup_data(email="pending@example.com"):
    return {"email": email, "password": PASSWORD, "password_confirm": PASSWORD}


def assert_limited(response, *, html=False, max_wait=60):
    assert response.status_code == 429
    assert 1 <= int(response["Retry-After"]) <= max_wait
    assert response["Cache-Control"] == "no-store"
    if html:
        assert response["Content-Type"].startswith("text/html")
        assert "Подождите немного" in response.content.decode()
    else:
        assert response.json() == {"detail": "Слишком частые запросы. Попробуйте позже."}


def test_api_site_and_admin_login_share_identity_budget(api_client, client, limits):
    limits["login"]["identity"] = (2, 60)
    assert (
        api_client.post(
            "/api/users/token/", {"email": "shared@example.com", "password": "wrong"}, REMOTE_ADDR="192.0.2.1"
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/login/", {"username": " SHARED@EXAMPLE.COM ", "password": "wrong"}, REMOTE_ADDR="192.0.2.2"
        ).status_code
        == 200
    )
    assert_limited(
        client.post("/admin/login/", {"username": "shared@example.com", "password": "wrong"}, REMOTE_ADDR="192.0.2.3"),
        html=True,
    )
    assert_limited(
        api_client.post(
            "/api/users/token/", {"email": "SHARED@EXAMPLE.COM", "password": "wrong"}, REMOTE_ADDR="192.0.2.4"
        )
    )


@pytest.mark.parametrize("url", ["/login/", "/admin/login/"])
def test_extra_email_cannot_spoof_html_login_identity(api_client, client, limits, url):
    limits["login"]["identity"] = (1, 60)
    assert (
        api_client.post(
            "/api/users/token/", {"email": "target@example.com", "password": "wrong"}, REMOTE_ADDR="192.0.2.1"
        ).status_code
        == 401
    )
    assert_limited(
        client.post(
            url,
            {"username": " TARGET@EXAMPLE.COM ", "email": "unrelated@example.com", "password": "wrong"},
            REMOTE_ADDR="192.0.2.2",
        ),
        html=True,
    )


def test_json_charset_does_not_split_email_budget(api_client, limits):
    limits["login"]["identity"] = (1, 60)
    data = {"email": "target@example.com", "password": "wrong", "extra": "café"}
    latin_body = json.dumps(data, ensure_ascii=False).encode("iso-8859-1")
    assert (
        api_client.post(
            "/api/users/token/",
            latin_body,
            content_type="application/json; charset=iso-8859-1",
            REMOTE_ADDR="192.0.2.1",
        ).status_code
        == 401
    )
    assert_limited(api_client.post("/api/users/token/", data, format="json", REMOTE_ADDR="192.0.2.2"))
    assert_limited(
        api_client.post(
            "/api/users/token/",
            latin_body,
            content_type="application/json; charset=iso-8859-1",
            REMOTE_ADDR="192.0.2.3",
        )
    )


def test_admin_nfkc_username_shares_ascii_login_budget(api_client, client, limits):
    limits["login"]["identity"] = (1, 60)
    assert (
        api_client.post(
            "/api/users/token/", {"email": "target@example.com", "password": "wrong"}, REMOTE_ADDR="192.0.2.1"
        ).status_code
        == 401
    )
    assert_limited(
        client.post(
            "/admin/login/", {"username": "ｔarget@example.com", "password": "wrong"}, REMOTE_ADDR="192.0.2.2"
        ),
        html=True,
    )
    assert "_auth_user_id" not in client.session


def test_signup_and_resend_share_email_budget_without_replacement_bypass(api_client, client, limits, mailoutbox):
    limits["signup"]["identity"] = (5, 3600)
    data = signup_data()
    assert api_client.post("/api/users/register/", data, REMOTE_ADDR="192.0.2.1").status_code == 201
    first = SignupRequest.objects.get(email=data["email"])
    assert len(mailoutbox) == 1

    assert client.post("/signup/", signup_data(" PENDING@EXAMPLE.COM "), REMOTE_ADDR="192.0.2.2").status_code == 302
    unchanged = SignupRequest.objects.get(email=data["email"])
    assert (unchanged.pk, unchanged.token) == (first.pk, first.token)
    assert len(mailoutbox) == 1

    assert (
        api_client.post(
            "/api/users/signup/resend/", {"email": "PENDING@EXAMPLE.COM"}, REMOTE_ADDR="192.0.2.3"
        ).status_code
        == 204
    )
    assert client.post("/signup/resend-verification/", REMOTE_ADDR="192.0.2.4").status_code == 302
    assert len(mailoutbox) == 1

    cooldown = api_client.post("/api/users/register/", data, REMOTE_ADDR="192.0.2.5")
    assert cooldown.status_code == 429
    assert 1 <= int(cooldown["Retry-After"]) <= 60
    assert_limited(api_client.post("/api/users/register/", data, REMOTE_ADDR="192.0.2.6"), max_wait=3600)
    unchanged = SignupRequest.objects.get(email=data["email"])
    assert (unchanged.pk, unchanged.token, unchanged.created_at) == (first.pk, first.token, first.created_at)
    assert len(mailoutbox) == 1


def test_rejected_repeat_registration_does_not_extend_resend_cooldown(api_client, limits, mailoutbox):
    start = int(time.time() // 3600) * 3600 + 10
    data = signup_data()
    with patch("users.rate_limit.time.time", return_value=start):
        assert api_client.post("/api/users/register/", data).status_code == 201
    first = SignupRequest.objects.get()
    with patch("users.rate_limit.time.time", return_value=start + 10):
        response = api_client.post("/api/users/register/", data)
        assert response.status_code == 429
        assert response["Retry-After"] == "50"
    assert SignupRequest.objects.get().token == first.token
    assert len(mailoutbox) == 1
    with patch("users.rate_limit.time.time", return_value=start + 59.5):
        assert api_client.post("/api/users/signup/resend/", {"email": data["email"]}).status_code == 204
    assert len(mailoutbox) == 1
    with patch("users.rate_limit.time.time", return_value=start + 60):
        assert api_client.post("/api/users/signup/resend/", {"email": data["email"]}).status_code == 204
    assert len(mailoutbox) == 2
    unchanged = SignupRequest.objects.get()
    assert (unchanged.token, unchanged.created_at) == (first.token, first.created_at)


def test_reset_short_alias_api_and_html_share_email_budget(api_client, client, user, limits, mailoutbox):
    limits["reset"]["identity"] = (2, 3600)
    assert (
        api_client.post("/users/reset_password/", {"email": user.email.upper()}, REMOTE_ADDR="192.0.2.1").status_code
        == 204
    )
    assert (
        api_client.post("/api/users/reset_password/", {"email": user.email}, REMOTE_ADDR="192.0.2.2").status_code
        == 204
    )
    assert len(mailoutbox) == 1
    assert_limited(
        client.post("/password-reset/", {"email": user.email}, REMOTE_ADDR="192.0.2.3"), html=True, max_wait=3600
    )
    assert_limited(
        api_client.post("/users/reset_password/", {"email": user.email}, REMOTE_ADDR="192.0.2.4"), max_wait=3600
    )
    assert len(mailoutbox) == 1


def test_reset_confirmation_aliases_share_ip_budget(api_client, limits):
    limits["confirm"]["ip"] = (2, 60)
    data = {"uid": "invalid", "token": "invalid", "new_password": PASSWORD}
    assert api_client.post("/api/users/reset_password_confirm/", data).status_code == 400
    assert api_client.post("/users/reset_password_confirm/", data).status_code == 400
    assert_limited(api_client.post("/users/reset_password_confirm", data))


def test_changing_emails_cannot_bypass_ip_budget(api_client, limits):
    limits["login"]["ip"] = (2, 60)
    for index in range(2):
        assert (
            api_client.post(
                "/api/users/token/", {"email": f"person{index}@example.com", "password": "wrong"}
            ).status_code
            == 401
        )
    assert_limited(api_client.post("/api/users/token/", {"email": "another@example.com", "password": "wrong"}))
    assert (
        api_client.post(
            "/api/users/token/", {"email": "another@example.com", "password": "wrong"}, REMOTE_ADDR="192.0.2.9"
        ).status_code
        == 401
    )


def test_get_forms_and_api_do_not_consume_post_budget(api_client, client, limits):
    limits["login"]["ip"] = (1, 60)
    for _ in range(3):
        assert client.get("/login/").status_code == 200
        assert api_client.get("/api/users/token/").status_code == 405
    data = {"email": "missing@example.com", "password": "wrong"}
    assert api_client.post("/api/users/token/", data).status_code == 401
    assert_limited(api_client.post("/api/users/token/", data))
    assert client.get("/login/").status_code == 200


@pytest.mark.parametrize("body", [b"{", b"[]", b"null", b'{"email": []}', b"\xff"])
def test_malformed_json_is_rejected_and_still_consumes_ip_budget(api_client, limits, body):
    limits["login"]["ip"] = (1, 60)
    assert api_client.post("/api/users/token/", body, content_type="application/json").status_code == 400
    assert_limited(api_client.post("/api/users/token/", body, content_type="application/json"))


def test_untrusted_forwarded_header_cannot_change_ip_budget(api_client, limits):
    limits["login"]["ip"] = (1, 60)
    data = {"email": "missing@example.com", "password": "wrong"}
    assert (
        api_client.post(
            "/api/users/token/", data, REMOTE_ADDR="192.0.2.9", HTTP_X_FORWARDED_FOR="198.51.100.1"
        ).status_code
        == 401
    )
    assert_limited(
        api_client.post("/api/users/token/", data, REMOTE_ADDR="192.0.2.9", HTTP_X_FORWARDED_FOR="198.51.100.2")
    )


def test_trusted_proxy_uses_last_forwarded_ip_and_ignores_spoofed_prefix(api_client, settings, limits):
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    limits["login"]["ip"] = (1, 60)
    data = {"email": "missing@example.com", "password": "wrong"}
    assert (
        api_client.post(
            "/api/users/token/", data, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.1, 192.0.2.9"
        ).status_code
        == 401
    )
    assert_limited(
        api_client.post(
            "/api/users/token/", data, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.2, 192.0.2.9"
        )
    )
    assert (
        api_client.post(
            "/api/users/token/", data, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.2, 192.0.2.10"
        ).status_code
        == 401
    )


def test_expired_counter_allows_request_and_retry_after_counts_down(api_client, limits):
    limits["login"]["ip"] = (1, 60)
    start = int(time.time() // 60) * 60
    data = {"email": "missing@example.com", "password": "wrong"}
    with patch("users.rate_limit.time.time", return_value=start):
        assert api_client.post("/api/users/token/", data).status_code == 401
        response = api_client.post("/api/users/token/", data)
        assert_limited(response)
        assert response["Retry-After"] == "60"
    with patch("users.rate_limit.time.time", return_value=start + 59.5):
        response = api_client.post("/api/users/token/", data)
        assert_limited(response)
        assert response["Retry-After"] == "1"
    with patch("users.rate_limit.time.time", return_value=start + 60):
        assert api_client.post("/api/users/token/", data).status_code == 401


def test_login_limit_does_not_consume_reset_budget(api_client, limits):
    limits["login"]["ip"] = (1, 60)
    limits["reset"]["ip"] = (1, 60)
    data = {"email": "missing@example.com", "password": "wrong"}
    assert api_client.post("/api/users/token/", data).status_code == 401
    assert_limited(api_client.post("/api/users/token/", data))
    assert api_client.post("/api/users/reset_password/", {"email": data["email"]}).status_code == 204
    assert_limited(api_client.post("/api/users/reset_password/", {"email": data["email"]}), max_wait=60)


@pytest.mark.django_db(transaction=True)
def test_database_cache_counter_is_atomic_across_workers():
    table = "test_auth_request_rate_cache"
    configuration = {"default": {"BACKEND": "messaging.cache.FixedExpiryDatabaseCache", "LOCATION": table}}
    barrier = Barrier(12, timeout=10)
    frozen_time = time.time()

    def attempt():
        close_old_connections()
        try:
            barrier.wait()
            return request_limit_wait("login:ip", "192.0.2.1", 3, 60)
        finally:
            close_old_connections()

    with override_settings(CACHES=configuration):
        call_command("createcachetable", stdout=StringIO())
        try:
            with patch("users.rate_limit.time.time", return_value=frozen_time):
                with ThreadPoolExecutor(max_workers=12) as executor:
                    futures = [executor.submit(attempt) for _ in range(12)]
                    results = [future.result(timeout=15) for future in futures]
            assert results.count(0) == 3
            assert sum(wait > 0 for wait in results) == 9
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE {connection.ops.quote_name(table)}")
