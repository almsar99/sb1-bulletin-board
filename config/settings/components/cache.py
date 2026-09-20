"""Встроенный кэш в памяти или общий кэш в базе данных без дополнительных драйверов."""

import re
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


def cache_from_env(environ):
    options = {"MAX_ENTRIES": 50000, "CULL_FREQUENCY": 10}
    value = environ.get("DJANGO_CACHE_URL", "").strip()
    if not value:
        return {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "bulletin-board",
            "OPTIONS": options,
        }
    parsed = urlsplit(value)
    if parsed.scheme == "locmem" and parsed.netloc and not parsed.path:
        return {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": parsed.netloc,
            "OPTIONS": options,
        }
    if parsed.scheme == "database" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parsed.netloc) and not parsed.path:
        return {"BACKEND": "messaging.cache.FixedExpiryDatabaseCache", "LOCATION": parsed.netloc, "OPTIONS": options}
    raise ImproperlyConfigured("DJANGO_CACHE_URL: используйте locmem://имя или database://имя_таблицы.")
