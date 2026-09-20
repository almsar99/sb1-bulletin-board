"""Настройки для развёрнутого сервиса.

Все значения читаются из окружения и проверяются при запуске: приложение не стартует
с неполной или небезопасной конфигурацией.
"""

import os

from .base import *  # noqa: F401,F403
from .components.database import database_from_env
from .components.email import email_from_env
from .components.environment import (
    allowed_hosts_from_env,
    comma_separated_values,
    required_secret,
    trusted_origins_from_env,
)
from .components.logging import deployment_logging_from_env
from .components.security import deployment_security_from_env

SECRET_KEY = required_secret(os.environ, "DJANGO_SECRET_KEY")

DEBUG = False

ALLOWED_HOSTS = allowed_hosts_from_env(os.environ)
CSRF_TRUSTED_ORIGINS = trusted_origins_from_env(os.environ)
CORS_ALLOWED_ORIGINS = comma_separated_values(os.environ, "DJANGO_CORS_ALLOWED_ORIGINS")

DATABASES = database_from_env(os.environ, require_credentials=True)
LOGGING = deployment_logging_from_env(os.environ)

globals().update(email_from_env(os.environ, allow_console=False))
globals().update(deployment_security_from_env(os.environ))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
