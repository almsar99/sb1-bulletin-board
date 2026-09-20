"""Атомарность PostgreSQL-кэша при одновременном захвате истёкшего ограничения."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier

import pytest
from django.core.cache import caches
from django.core.management import call_command
from django.db import connection, connections
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)
CACHE_TABLE = "atomic_rate_cache_test"


@pytest.fixture
def database_cache(settings, isolated_cache):
    settings.CACHES = {
        "default": {
            "BACKEND": "messaging.cache.FixedExpiryDatabaseCache",
            "LOCATION": CACHE_TABLE,
            "TIMEOUT": 45,
            "KEY_PREFIX": "atomic-tests",
            "VERSION": 3,
        }
    }
    call_command("createcachetable", stdout=StringIO())
    backend = caches["default"]
    backend.clear()
    yield backend
    backend.clear()


def stored_expiry(backend, key, version=None):
    with connection.cursor() as cursor:
        cursor.execute(
            f'SELECT "expires" FROM {connection.ops.quote_name(CACHE_TABLE)} WHERE "cache_key" = %s',
            [backend.make_key(key, version)],
        )
        return cursor.fetchone()[0]


@pytest.mark.parametrize("existing", [False, True], ids=["missing", "expired"])
def test_concurrent_add_has_exactly_one_winner(database_cache, existing):
    if existing:
        database_cache.set("quota", "expired", timeout=-60)
    workers = 8
    ready_to_write = Barrier(workers, timeout=10)

    def add_value(value):
        worker_connection = connections["default"]

        def synchronize_writes(execute, sql, params, many, context):
            if CACHE_TABLE in sql and sql.lstrip().upper().startswith(("INSERT", "UPDATE")):
                # Все соединения доходят до записи до того, как первое её выполнит.
                # При прежнем SELECT+UPDATE они уже прочитали один истёкший срок.
                ready_to_write.wait()
            return execute(sql, params, many, context)

        try:
            with worker_connection.execute_wrapper(synchronize_writes):
                return value, caches["default"].add("quota", value, timeout=600)
        finally:
            worker_connection.close()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(add_value, range(workers)))

    winners = [value for value, added in results if added is True]
    assert len(winners) == 1
    assert sum(added is False for _, added in results) == workers - 1
    assert database_cache.get("quota") == winners[0]


def test_add_and_increment_preserve_existing_expiry(database_cache):
    assert database_cache.add("counter", 1, timeout=60)
    expires = stored_expiry(database_cache, "counter")

    assert database_cache.add("counter", 999, timeout=3600) is False
    assert database_cache.get("counter") == 1
    assert stored_expiry(database_cache, "counter") == expires
    assert database_cache.incr("counter") == 2
    assert stored_expiry(database_cache, "counter") == expires

    database_cache.set("counter", 5, timeout=120)
    assert database_cache.get("counter") == 5
    assert stored_expiry(database_cache, "counter") > expires


def test_add_keeps_django_timeout_and_serialization_semantics(database_cache):
    before = timezone.now()
    value = {"текст": "Письмо", "items": [1, None, True]}
    assert database_cache.add("default-timeout", value)
    assert database_cache.get("default-timeout") == value
    assert before + timedelta(seconds=44) <= stored_expiry(database_cache, "default-timeout")
    assert stored_expiry(database_cache, "default-timeout") <= timezone.now() + timedelta(seconds=45)

    assert database_cache.add("no-expiry", value, timeout=None, version=9)
    assert database_cache.get("no-expiry", version=9) == value
    assert database_cache.get("no-expiry") is None
    assert stored_expiry(database_cache, "no-expiry", version=9).year == 9999
    assert database_cache.add("zero-timeout", value, timeout=0)
    assert database_cache.get("zero-timeout") is None


def test_add_preserves_configured_culling(database_cache):
    database_cache._max_entries = 1
    database_cache._cull_frequency = 0
    assert database_cache.add("first", 1)
    assert database_cache.add("second", 2)

    assert database_cache.add("third", 3)

    assert database_cache.get_many(["first", "second", "third"]) == {"third": 3}
