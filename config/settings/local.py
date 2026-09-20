"""Настройки для локальной разработки."""

from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
