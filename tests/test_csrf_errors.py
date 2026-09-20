"""Восстановление после отклонённой формы без обхода защиты и утечки данных."""

import re

import pytest
from django.test import Client

from tests.conftest import PASSWORD
from users.models import SignupRequest, User

pytestmark = pytest.mark.django_db
ORIGIN = "https://testserver"


def form_token(response):
    return re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode()).group(1)


def assert_friendly_failure(response, retry_url):
    assert response.status_code == 403
    assert "errors/csrf.html" in [template.name for template in response.templates]
    html = response.content.decode()
    assert "маркет.код" in html
    assert "Не удалось отправить форму" in html
    assert "Открыть форму заново" in html
    assert response.context["retry_url"] == retry_url
    assert f'href="{retry_url}"' in html
    assert "no-store" in response["Cache-Control"]
    for private_value in (PASSWORD, "DEBUG", "Ошибка проверки CSRF", "CSRF verification failed", "attacker.example"):
        assert private_value not in html


@pytest.mark.parametrize(
    "debug,failure",
    [
        (False, "missing_cookie"),
        (False, "missing_token"),
        (False, "mismatched_token"),
        (False, "null_origin"),
        (False, "foreign_origin"),
        (True, "missing_cookie"),
    ],
)
def test_csrf_failure_preserves_logged_out_state(user, settings, debug, failure):
    settings.DEBUG = debug
    browser = Client(enforce_csrf_checks=True)
    data = {"username": user.email, "password": PASSWORD}
    origin = ORIGIN
    if failure != "missing_cookie":
        token = form_token(browser.get("/login/", secure=True))
        if failure != "missing_token":
            data["csrfmiddlewaretoken"] = token
        if failure == "mismatched_token":
            data["csrfmiddlewaretoken"] = ("A" if token[0] != "A" else "B") + token[1:]
        if failure == "null_origin":
            origin = "null"
        if failure == "foreign_origin":
            origin = "https://attacker.example"

    response = browser.post("/login/", data, secure=True, HTTP_ORIGIN=origin)

    assert_friendly_failure(response, "/login/")
    assert "_auth_user_id" not in browser.session
    user.refresh_from_db()
    assert user.last_login is None
    assert user.check_password(PASSWORD)


def test_stale_login_form_can_recover_with_a_fresh_form(user):
    browser = Client(enforce_csrf_checks=True)
    stale_token = form_token(browser.get("/login/", secure=True))
    credentials = {"username": user.email, "password": PASSWORD}
    # Вход в другой вкладке меняет CSRF-cookie, а старая форма остаётся открытой.
    logged_in = browser.post(
        "/login/", {**credentials, "csrfmiddlewaretoken": stale_token}, secure=True, HTTP_ORIGIN=ORIGIN
    )
    assert logged_in.status_code == 302
    assert browser.session["_auth_user_id"] == str(user.pk)
    current_token = form_token(browser.get("/account/", secure=True))
    logged_out = browser.post("/logout/", {"csrfmiddlewaretoken": current_token}, secure=True, HTTP_ORIGIN=ORIGIN)
    assert logged_out.status_code == 302
    assert "_auth_user_id" not in browser.session

    rejected = browser.post(
        "/login/", {**credentials, "csrfmiddlewaretoken": stale_token}, secure=True, HTTP_ORIGIN=ORIGIN
    )
    assert_friendly_failure(rejected, "/login/")
    assert "_auth_user_id" not in browser.session

    fresh_form = browser.get(rejected.context["retry_url"], secure=True)
    assert fresh_form.status_code == 200
    recovered = browser.post(
        "/login/",
        {**credentials, "csrfmiddlewaretoken": form_token(fresh_form)},
        secure=True,
        HTTP_ORIGIN=ORIGIN,
    )
    assert recovered.status_code == 302
    assert browser.session["_auth_user_id"] == str(user.pk)


def test_https_login_accepts_same_origin_referer_without_origin_header(user):
    browser = Client(enforce_csrf_checks=True)
    token = form_token(browser.get("/login/", secure=True))

    response = browser.post(
        "/login/",
        {"username": user.email, "password": PASSWORD, "csrfmiddlewaretoken": token},
        secure=True,
        HTTP_REFERER=f"{ORIGIN}/login/",
    )

    assert response.status_code == 302
    assert browser.session["_auth_user_id"] == str(user.pk)


@pytest.mark.parametrize(
    ("path", "retry_url"),
    [
        ("/signup/", "/signup/"),
        ("/admin/login/", "/admin/login/"),
        ("/password-reset/", "/password-reset/"),
        ("/password-reset/invalid/invalid/", "/password-reset/"),
        ("/ad/new/", "/login/"),
    ],
)
def test_recovery_link_uses_a_local_safe_destination(path, retry_url, admin, mailoutbox):
    browser = Client(enforce_csrf_checks=True)
    response = browser.post(
        f"{path}?next=https://attacker.example/",
        {"username": admin.email, "email": admin.email, "password": PASSWORD, "next": "https://attacker.example/"},
        secure=True,
        HTTP_ORIGIN=ORIGIN,
        HTTP_REFERER="https://attacker.example/",
    )

    assert_friendly_failure(response, retry_url)
    assert "_auth_user_id" not in browser.session
    assert User.objects.count() == 1
    assert not SignupRequest.objects.exists()
    assert not mailoutbox


def test_rejected_logout_preserves_session_and_offers_account(user):
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(user)

    response = browser.post("/logout/", secure=True, HTTP_ORIGIN=ORIGIN)

    assert_friendly_failure(response, "/account/")
    assert browser.session["_auth_user_id"] == str(user.pk)
