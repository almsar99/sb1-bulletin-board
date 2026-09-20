"""Готовность сервиса и запуск с эксплуатационными настройками."""

import importlib
import logging.config
import os
import subprocess
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import OperationalError, connection
from django.test import override_settings

from config.settings.components.logging import deployment_logging_from_env
from messaging.cache import FixedExpiryDatabaseCache


@pytest.mark.django_db
def test_health_checks_database(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_database_unavailable(client):
    with patch("config.views.connection.cursor", side_effect=OperationalError("private connection detail")):
        response = client.get("/health/")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"private" not in response.content


def test_production_logging_is_self_contained():
    settings = deployment_logging_from_env({"DJANGO_LOG_LEVEL": "warning"})
    formatter = logging.config.DictConfigurator(settings).configure_formatter(settings["formatters"]["standard"])
    record = logging.LogRecord("service", logging.WARNING, "", 1, "Message", (), None)
    assert "WARNING service: Message" in formatter.format(record)


def test_container_uses_safe_gunicorn_logger():
    import json

    from gunicorn.config import Config

    from config.gunicorn import SafeLogger

    command = next(
        line.removeprefix("CMD ") for line in Path("Dockerfile").read_text().splitlines() if line.startswith("CMD ")
    )
    args = json.loads(command)
    config = Config()
    config.set("logger_class", args[args.index("--logger-class") + 1])
    assert config.logger_class is SafeLogger
    assert args[args.index("--access-logfile") + 1] == "-"


def test_production_settings_pass_deployment_checks(monkeypatch, smtp_env, security_env):
    # Параметры только для проверки конфигурации; сетевых обращений к SMTP нет.
    env = {
        **smtp_env,
        **security_env,
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": "configuration-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz",
        "DJANGO_ALLOWED_HOSTS": "example.com,localhost",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.com",
        "FRONTEND_URL": "https://example.com",
        "POSTGRES_DB": "bulletin_board",
        "POSTGRES_USER": "bulletin_board",
        "POSTGRES_PASSWORD": "test-password",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "5432",
        "POSTGRES_SSLMODE": "prefer",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    module = importlib.import_module("config.settings.prod")
    assert module.DEBUG is False
    result = subprocess.run(
        [os.sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("command", ["migrate", "createcachetable", "collectstatic"])
def test_entrypoint_stops_when_initialization_fails(tmp_path, command):
    python = tmp_path / "python"
    python.write_text(
        '#!/bin/sh\nif [ "$1" = "-" ]; then cat >/dev/null; exit 0; fi\n'
        f'if [ "$2" = "{command}" ]; then exit 19; fi\nexit 0\n'
    )
    python.chmod(0o755)
    result = subprocess.run(
        ["sh", "deploy/entrypoint.sh", "echo", "APPLICATION_STARTED"],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 19
    assert "APPLICATION_STARTED" not in result.stdout


def test_entrypoint_prepares_shared_cache_before_starting_application(tmp_path):
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nif [ "$1" = "-" ]; then cat >/dev/null; exit 0; fi\n' 'printf "%s\\n" "$2"\n')
    python.chmod(0o755)
    result = subprocess.run(
        ["sh", "deploy/entrypoint.sh", "echo", "APPLICATION_STARTED"],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["migrate", "createcachetable", "collectstatic", "APPLICATION_STARTED"]


@pytest.mark.django_db(transaction=True)
def test_shared_cache_initialization_is_idempotent_and_keeps_rate_limits():
    table = "test_deployment_rate_cache"
    cache_settings = {"default": {"BACKEND": "messaging.cache.FixedExpiryDatabaseCache", "LOCATION": table}}
    with override_settings(CACHES=cache_settings):
        call_command("createcachetable", stdout=StringIO())
        try:
            first_worker = FixedExpiryDatabaseCache(table, {})
            second_worker = FixedExpiryDatabaseCache(table, {})
            assert first_worker.add("signup-resend", 1, timeout=60)
            call_command("createcachetable", stdout=StringIO())
            assert not second_worker.add("signup-resend", 1, timeout=60)
            assert first_worker.add("message-count", 1, timeout=60)
            assert second_worker.incr("message-count") == 2
            assert first_worker.get("message-count") == 2
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE {connection.ops.quote_name(table)}")
