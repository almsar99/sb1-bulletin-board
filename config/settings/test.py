"""Настройки для автоматических тестов."""

import os

from .base import *  # noqa: F401,F403
from .components.database import database_from_env

DEBUG = False

ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]

FRONTEND_URL = "http://testserver"

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

DATABASES = database_from_env(os.environ, require_credentials=False)
