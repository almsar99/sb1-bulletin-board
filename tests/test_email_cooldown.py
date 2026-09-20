"""Минутная пауза писем: границы времени и общий кэш нескольких процессов."""

from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import override_settings

from tests.conftest import PASSWORD
from users.models import SignupRequest
from users.rate_limit import acquire_email_send, email_send_wait
from users.signup import EmailSendCooldown, submit_signup_request

EMAIL = "pending@example.com"
START = 1800000000.875
CACHE_TABLE = "test_email_deadline_cache"


@pytest.fixture
def database_cache():
    configuration = {"default": {"BACKEND": "messaging.cache.FixedExpiryDatabaseCache", "LOCATION": CACHE_TABLE}}
    with override_settings(CACHES=configuration):
        call_command("createcachetable", stdout=StringIO())
        try:
            yield cache
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE {connection.ops.quote_name(CACHE_TABLE)}")


def test_cooldown_counts_down():
    purpose = "signup"
    with patch("users.rate_limit.time.time", return_value=START):
        assert email_send_wait(purpose, EMAIL) == 0
        assert acquire_email_send(purpose, EMAIL) == 0
        assert email_send_wait(purpose, EMAIL.upper()) == 60
    for elapsed, wait in ((0, 60), (30.1, 30), (59.999, 1)):
        with patch("users.rate_limit.time.time", return_value=START + elapsed):
            assert acquire_email_send(purpose, f" {EMAIL.upper()} ") == wait
            assert email_send_wait(purpose, EMAIL) == wait
    with patch("users.rate_limit.time.time", return_value=START + 60):
        assert email_send_wait(purpose, EMAIL) == 0
        assert acquire_email_send(purpose, EMAIL) == 0
        assert email_send_wait(purpose, EMAIL) == 60


def test_cooldowns_are_independent_for_different_emails_and_purposes():
    assert acquire_email_send("signup", EMAIL) == 0
    assert acquire_email_send("reset", EMAIL) == 0
    assert acquire_email_send("signup", "another@example.com") == 0
    assert acquire_email_send("signup", EMAIL) > 0


@pytest.mark.django_db(transaction=True)
def test_database_cooldown_preserves_fraction(database_cache):
    purpose = "signup"
    with patch("users.rate_limit.time.time", return_value=START):
        assert acquire_email_send(purpose, EMAIL) == 0
    # Обычный DatabaseCache округляет expires вниз: проверяем оставшуюся долю секунды.
    with patch("users.rate_limit.time.time", return_value=START + 59.5):
        assert email_send_wait(purpose, EMAIL) == 1
        assert acquire_email_send(purpose, EMAIL) == 1
    with patch("users.rate_limit.time.time", return_value=START + 60):
        assert email_send_wait(purpose, EMAIL) == 0
        assert acquire_email_send(purpose, EMAIL) == 0
        assert email_send_wait(purpose, EMAIL) == 60


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("expired", [False, True])
def test_database_cooldown_has_one_winner_across_workers(database_cache, expired):
    if expired:
        with patch("users.rate_limit.time.time", return_value=START - 60):
            assert acquire_email_send("signup", EMAIL) == 0
    barrier = Barrier(8, timeout=10)

    def attempt():
        close_old_connections()
        try:
            barrier.wait()
            email_send_wait("signup", EMAIL)
            return acquire_email_send("signup", EMAIL)
        finally:
            close_old_connections()

    with patch("users.rate_limit.time.time", return_value=START):
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: attempt(), range(8)))
        assert results.count(0) == 1
        assert results.count(60) == 7
        assert email_send_wait("signup", EMAIL) == 60


@pytest.mark.django_db(transaction=True)
def test_database_parallel_registration_sends_one_link(database_cache, mailoutbox):
    barrier = Barrier(4, timeout=10)

    def register():
        close_old_connections()
        try:
            barrier.wait()
            try:
                return submit_signup_request(email=EMAIL, password=PASSWORD).token
            except EmailSendCooldown:
                return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=4) as executor:
        tokens = list(executor.map(lambda _: register(), range(4)))
    assert sum(token is not None for token in tokens) == 1
    signup = SignupRequest.objects.get(email=EMAIL)
    assert signup.token == next(token for token in tokens if token is not None)
    assert len(mailoutbox) == 1
    assert signup.token in mailoutbox[0].body


def test_failed_cache_reservation_fails_closed():
    with patch("users.rate_limit.cache.add", return_value=False):
        assert acquire_email_send("signup", EMAIL) > 0
