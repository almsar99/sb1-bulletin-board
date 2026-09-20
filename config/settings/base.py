"""Общие настройки проекта.

Значения, зависящие от окружения, вынесены в модули пакета components.
"""

import os
from datetime import timedelta
from pathlib import Path

from .components.dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from .components.cache import cache_from_env  # noqa: E402
from .components.database import DATABASES  # noqa: E402
from .components.email import EMAIL_BACKEND  # noqa: E402
from .components.environment import (  # noqa: E402
    boolean_value,
    comma_separated_values,
    integer_value,
    required_secret,
)
from .components.logging import LOGGING  # noqa: E402
from .components.security import (  # noqa: E402
    CSRF_COOKIE_HTTPONLY,
    SECURE_CONTENT_TYPE_NOSNIFF,
    SECURE_CROSS_ORIGIN_OPENER_POLICY,
    SECURE_REFERRER_POLICY,
    SESSION_COOKIE_HTTPONLY,
    X_FRAME_OPTIONS,
)
from .components.storage import STORAGES  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = required_secret(os.environ, "DJANGO_SECRET_KEY")

DEBUG = False

CACHES = {"default": cache_from_env(os.environ)}

ALLOWED_HOSTS = comma_separated_values(os.environ, "DJANGO_ALLOWED_HOSTS") or ["127.0.0.1", "localhost"]

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://127.0.0.1:8000")
DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_DEFAULT_FROM_EMAIL", "noreply@market-kod.ru")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "users.apps.UsersConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "users.middleware.AuthenticationRateLimitMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Модель пользователя объявляется до создания первых миграций приложений.
AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("users.authentication.PasswordAwareJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 4,
}

PASSWORD_RESET_TIMEOUT = 24 * 60 * 60

# Общие окна для API, коротких алиасов и HTML-форм; значения — (запросы, секунды).
AUTH_REQUEST_LIMITS = {
    "login": {"ip": (20, 60), "identity": (10, 60)},
    "signup": {"ip": (20, 60)},
    "reset": {"ip": (20, 60)},
    "confirm": {"ip": (30, 60)},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=integer_value(os.environ, "JWT_ACCESS_TTL_MINUTES", default=60, minimum=15, maximum=60)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=integer_value(os.environ, "JWT_REFRESH_TTL_DAYS", default=7, minimum=1, maximum=7)
    ),
    "UPDATE_LAST_LOGIN": True,
    "TOKEN_REFRESH_SERIALIZER": "users.authentication.PasswordAwareRefreshSerializer",
    "TOKEN_VERIFY_SERIALIZER": "users.authentication.PasswordAwareVerifySerializer",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Доска объявлений",
    "DESCRIPTION": (
        "Сервис размещения объявлений: учётные записи с ролями, объявления, "
        "отзывы и поиск. Регистрация, получение токенов, восстановление пароля и список "
        "объявлений доступны без авторизации. Остальные методы требуют заголовок "
        "Authorization: Bearer <access_token>."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api",
}

CORS_ALLOWED_ORIGINS = comma_separated_values(os.environ, "DJANGO_CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = boolean_value(os.environ, "DJANGO_CORS_ALLOW_CREDENTIALS", default=False)

__all__ = [
    "ALLOWED_HOSTS",
    "CACHES",
    "AUTH_PASSWORD_VALIDATORS",
    "AUTH_REQUEST_LIMITS",
    "AUTH_USER_MODEL",
    "ASGI_APPLICATION",
    "BASE_DIR",
    "CORS_ALLOWED_ORIGINS",
    "CORS_ALLOW_CREDENTIALS",
    "CSRF_COOKIE_HTTPONLY",
    "DATABASES",
    "DEBUG",
    "DEFAULT_AUTO_FIELD",
    "EMAIL_BACKEND",
    "INSTALLED_APPS",
    "LANGUAGE_CODE",
    "LOGGING",
    "MEDIA_ROOT",
    "MEDIA_URL",
    "MIDDLEWARE",
    "REST_FRAMEWORK",
    "PASSWORD_RESET_TIMEOUT",
    "ROOT_URLCONF",
    "SECRET_KEY",
    "SECURE_CONTENT_TYPE_NOSNIFF",
    "SECURE_CROSS_ORIGIN_OPENER_POLICY",
    "SECURE_REFERRER_POLICY",
    "SESSION_COOKIE_HTTPONLY",
    "SIMPLE_JWT",
    "FRONTEND_URL",
    "DEFAULT_FROM_EMAIL",
    "SPECTACULAR_SETTINGS",
    "STATIC_ROOT",
    "STATIC_URL",
    "STORAGES",
    "TEMPLATES",
    "TIME_ZONE",
    "USE_I18N",
    "USE_TZ",
    "WSGI_APPLICATION",
    "X_FRAME_OPTIONS",
]
