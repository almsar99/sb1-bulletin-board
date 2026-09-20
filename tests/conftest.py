"""Общие фикстуры тестов."""

import pytest
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from rest_framework.test import APIClient

from users.models import SignupRequest, User, UserRole

PASSWORD = "Str0ng-Passphrase-2026"


@pytest.fixture
def signup_request_factory(db):
    def create(*, email="pending@example.com", password=PASSWORD, **fields):
        return SignupRequest.objects.create(email=email, password=make_password(password), **fields)

    return create


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="user@example.com",
        password=PASSWORD,
        email_verified_at=timezone.now(),
        first_name="Иван",
        last_name="Петров",
        phone="+79990000001",
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        email="other@example.com",
        password=PASSWORD,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.com",
        password=PASSWORD,
        email_verified_at=timezone.now(),
        role=UserRole.ADMIN,
        is_staff=True,
    )


def authenticate(client, account):
    client.force_authenticate(user=account)
    return client


@pytest.fixture
def user_client(api_client, user):
    return authenticate(api_client, user)


@pytest.fixture
def other_client(other_user):
    client = APIClient()
    client.force_authenticate(user=other_user)
    return client


@pytest.fixture
def admin_client(admin):
    client = APIClient()
    client.force_authenticate(user=admin)
    return client


@pytest.fixture
def ad(user):
    from ads.models import Ad

    return Ad.objects.create(title="Велосипед", price=15000, description="Городской велосипед", author=user)


@pytest.fixture
def review(user, ad):
    from ads.models import Review

    return Review.objects.create(text="Отличное предложение", author=user, ad=ad)


@pytest.fixture
def smtp_env():
    return {
        "DJANGO_EMAIL_BACKEND": "smtp",
        "DJANGO_EMAIL_USE_TLS": "true",
        "DJANGO_EMAIL_USE_SSL": "false",
        "DJANGO_EMAIL_HOST": "smtp.example.com",
        "DJANGO_EMAIL_PORT": "587",
        "DJANGO_EMAIL_HOST_USER": "user",
        "DJANGO_EMAIL_HOST_PASSWORD": "password",
        "DJANGO_DEFAULT_FROM_EMAIL": "noreply@example.com",
        "DJANGO_SERVER_EMAIL": "errors@example.com",
    }


@pytest.fixture
def security_env():
    return {
        "DJANGO_SECURE_HSTS_SECONDS": "31536000",
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS": "true",
        "DJANGO_SECURE_HSTS_PRELOAD": "true",
        "DJANGO_BEHIND_TLS_PROXY": "true",
    }


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    from django.core.cache import cache

    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}}
    cache.clear()
    yield
    cache.clear()
