"""Настройки JWT загружаются только с допустимыми сроками токенов."""

import json
import os
import subprocess
import sys

import pytest


def load_jwt_settings(access_minutes, refresh_days):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from config.settings.base import SIMPLE_JWT; "
            "print(json.dumps([SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds(), "
            "SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()]))",
        ],
        env={
            **os.environ,
            "DJANGO_SECRET_KEY": "jwt-configuration-test-secret-0123456789-abcdefghijklmnopqrstuvwxyz",
            "JWT_ACCESS_TTL_MINUTES": str(access_minutes),
            "JWT_REFRESH_TTL_DAYS": str(refresh_days),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("access_minutes,refresh_days", [(15, 1), (60, 7)])
def test_jwt_lifetimes_from_environment(access_minutes, refresh_days):
    result = load_jwt_settings(access_minutes, refresh_days)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [access_minutes * 60, refresh_days * 86400]


@pytest.mark.parametrize(
    "access_minutes,refresh_days,variable",
    [
        (14, 7, "JWT_ACCESS_TTL_MINUTES"),
        (61, 7, "JWT_ACCESS_TTL_MINUTES"),
        (60, 0, "JWT_REFRESH_TTL_DAYS"),
        (60, 8, "JWT_REFRESH_TTL_DAYS"),
    ],
)
def test_jwt_rejects_invalid_lifetimes(access_minutes, refresh_days, variable):
    result = load_jwt_settings(access_minutes, refresh_days)
    assert result.returncode != 0
    assert "EnvironmentConfigurationError" in result.stderr
    assert variable in result.stderr
