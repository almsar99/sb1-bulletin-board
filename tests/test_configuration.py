"""Проверки контракта переменных окружения и защиты эксплуатации."""

import re
from urllib.parse import urlsplit

import pytest

from config.settings.components.database import database_from_env
from config.settings.components.email import email_from_env
from config.settings.components.environment import (
    EnvironmentConfigurationError,
    allowed_hosts_from_env,
    boolean_value,
    choice_value,
    comma_separated_values,
    integer_value,
    required_secret,
    required_value,
    trusted_origins_from_env,
)
from config.settings.components.security import deployment_security_from_env


@pytest.mark.parametrize("value", [None, "  "])
def test_required_value_rejects_missing(value):
    with pytest.raises(EnvironmentConfigurationError):
        required_value({} if value is None else {"KEY": value}, "KEY")


def test_secret_minimum_length():
    with pytest.raises(EnvironmentConfigurationError):
        required_secret({"KEY": "short"}, "KEY")
    assert required_secret({"KEY": "x" * 50}, "KEY") == "x" * 50


@pytest.mark.parametrize("value,expected", [("YES", True), ("0", False)])
def test_boolean(value, expected):
    assert boolean_value({"KEY": value}, "KEY") is expected


@pytest.mark.parametrize("value", [None, "unknown"])
def test_invalid_boolean(value):
    with pytest.raises(EnvironmentConfigurationError):
        boolean_value({} if value is None else {"KEY": value}, "KEY", required=True)


@pytest.mark.parametrize("value", [None, "a", "-1", "11"])
def test_invalid_integer(value):
    with pytest.raises(EnvironmentConfigurationError):
        integer_value({} if value is None else {"KEY": value}, "KEY", required=True, minimum=0, maximum=10)


@pytest.mark.parametrize("value", [None, "wrong"])
def test_invalid_choice(value):
    with pytest.raises(EnvironmentConfigurationError):
        choice_value({} if value is None else {"KEY": value}, "KEY", {"one"}, required=True)


@pytest.mark.parametrize("value", [None, "a,", "a,a"])
def test_invalid_list(value):
    with pytest.raises(EnvironmentConfigurationError):
        comma_separated_values({} if value is None else {"KEY": value}, "KEY", required=True)


@pytest.mark.parametrize("host", ["*", ".example.com", "https://example.com", "bad_host", "example.com/path"])
def test_unsafe_hosts(host):
    with pytest.raises(EnvironmentConfigurationError):
        allowed_hosts_from_env({"DJANGO_ALLOWED_HOSTS": host})


def test_exact_hosts():
    assert allowed_hosts_from_env({"DJANGO_ALLOWED_HOSTS": "example.com,127.0.0.1,[::1]"}) == [
        "example.com",
        "127.0.0.1",
        "[::1]",
    ]


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://*.example.com",
        "https://example.com/",
        "https://u:p@example.com",
        "https://example.com:99999",
        "https://bad_host",
        "https://example.com:0",
    ],
)
def test_unsafe_origins(origin):
    with pytest.raises(EnvironmentConfigurationError):
        trusted_origins_from_env({"DJANGO_CSRF_TRUSTED_ORIGINS": origin})


def test_exact_https_origin():
    assert trusted_origins_from_env({"DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.com"}) == ["https://example.com"]


def test_smtp_requires_encryption(smtp_env):
    assert email_from_env(smtp_env, allow_console=False)["EMAIL_USE_TLS"] is True
    smtp_env["DJANGO_EMAIL_USE_TLS"] = "false"
    with pytest.raises(EnvironmentConfigurationError):
        email_from_env(smtp_env, allow_console=False)


def test_console_only_in_development():
    env = {"DJANGO_EMAIL_BACKEND": "console"}
    assert "console" in email_from_env(env, allow_console=True)["EMAIL_BACKEND"]
    with pytest.raises(EnvironmentConfigurationError):
        email_from_env(env, allow_console=False)


def test_production_security(security_env):
    settings = deployment_security_from_env(security_env)
    assert settings["SECURE_SSL_REDIRECT"]
    assert settings["SESSION_COOKIE_SECURE"]
    assert settings["CSRF_COOKIE_SECURE"]
    assert settings["SECURE_PROXY_SSL_HEADER"] == ("HTTP_X_FORWARDED_PROTO", "https")


@pytest.mark.django_db
def test_csp_protects_site_and_allows_documentation_assets(client, settings, security_env):
    policy = deployment_security_from_env(security_env)["SECURE_CSP"]
    settings.SECURE_CSP = policy

    site = client.get("/")
    documentation = client.get("/api/docs/")

    assert site.status_code == documentation.status_code == 200
    for response in (site, documentation):
        assert "default-src 'self'" in response["Content-Security-Policy"]
        assert "object-src 'none'" in response["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in response["Content-Security-Policy"]
        assert "yandex.ru" not in response["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" not in site["Content-Security-Policy"]
    directives = {
        parts[0]: parts[1:]
        for directive in documentation["Content-Security-Policy"].split(";")
        if (parts := directive.split())
    }
    assets = re.findall(r'(?:src|href)="(https://[^\"]+)"', documentation.content.decode())
    assert assets
    for asset in assets:
        parsed = urlsplit(asset)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        directive = (
            "script-src" if parsed.path.endswith(".js") else "style-src" if parsed.path.endswith(".css") else "img-src"
        )
        assert origin in directives[directive]
    assert settings.SECURE_CSP == deployment_security_from_env(security_env)["SECURE_CSP"]


@pytest.mark.parametrize("seconds", ["0", "300"])
def test_unsafe_hsts_preload(security_env, seconds):
    security_env["DJANGO_SECURE_HSTS_SECONDS"] = seconds
    with pytest.raises(EnvironmentConfigurationError):
        deployment_security_from_env(security_env)


def test_production_database():
    env = {
        "POSTGRES_DB": "db",
        "POSTGRES_USER": "user",
        "POSTGRES_PASSWORD": "password",
        "POSTGRES_HOST": "db",
        "POSTGRES_PORT": "5432",
        "POSTGRES_SSLMODE": "require",
    }
    configuration = database_from_env(env, require_credentials=True)["default"]
    assert configuration["CONN_HEALTH_CHECKS"]
    assert configuration["OPTIONS"] == {"connect_timeout": 2, "sslmode": "require"}
    del env["POSTGRES_PASSWORD"]
    with pytest.raises(EnvironmentConfigurationError):
        database_from_env(env, require_credentials=True)
