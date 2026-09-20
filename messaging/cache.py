"""Атомарные операции PostgreSQL-кэша с сохранением срока действия счётчиков."""

import base64
import pickle
from datetime import UTC, datetime

from django.conf import settings
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.db import DatabaseCache
from django.db import DatabaseError, connections, router, transaction
from django.utils import timezone


class FixedExpiryDatabaseCache(DatabaseCache):
    def add_deadline(self, key, deadline, now):
        """Атомарная пауза с точностью до микросекунд, включая границу expiry."""
        key = self.make_and_validate_key(key)
        alias = router.db_for_write(self.cache_model_class)
        connection = connections[alias]
        table = connection.ops.quote_name(self._table)
        tz = UTC if settings.USE_TZ else None
        now = datetime.fromtimestamp(now, tz=tz)
        expires = datetime.fromtimestamp(deadline, tz=tz)
        encoded = base64.b64encode(pickle.dumps(deadline, self.pickle_protocol)).decode("latin1")
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            if count > self._max_entries:
                self._cull(alias, cursor, now, count)
            try:
                with transaction.atomic(using=alias):
                    cursor.execute(
                        f'INSERT INTO {table} AS entry ("cache_key", "value", "expires") VALUES (%s, %s, %s) '
                        'ON CONFLICT ("cache_key") DO UPDATE '
                        'SET "value" = EXCLUDED."value", "expires" = EXCLUDED."expires" '
                        'WHERE entry."expires" <= %s RETURNING "cache_key"',
                        [
                            key,
                            encoded,
                            connection.ops.adapt_datetimefield_value(expires),
                            connection.ops.adapt_datetimefield_value(now),
                        ],
                    )
                    return cursor.fetchone() is not None
            except DatabaseError:
                return False

    def get_deadline(self, key):
        """Прочитать срок без удаления: другой процесс мог уже продлить запись."""
        key = self.make_and_validate_key(key)
        alias = router.db_for_read(self.cache_model_class)
        connection = connections[alias]
        table = connection.ops.quote_name(self._table)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT "value" FROM {table} WHERE "cache_key" = %s', [key])
            row = cursor.fetchone()
        return pickle.loads(base64.b64decode(row[0])) if row else None

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        key = self.make_and_validate_key(key, version)
        timeout = self.get_backend_timeout(timeout)
        alias = router.db_for_write(self.cache_model_class)
        connection = connections[alias]
        table = connection.ops.quote_name(self._table)
        now = timezone.now().replace(microsecond=0)
        expires = (
            datetime.max if timeout is None else datetime.fromtimestamp(timeout, tz=UTC if settings.USE_TZ else None)
        )
        encoded = base64.b64encode(pickle.dumps(value, self.pickle_protocol)).decode("latin1")
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            if count > self._max_entries:
                self._cull(alias, cursor, now, count)
            try:
                with transaction.atomic(using=alias):
                    # Унаследованный add сначала читает expires, затем обновляет
                    # строку: несколько процессов могут одновременно увидеть expiry.
                    cursor.execute(
                        f'INSERT INTO {table} AS entry ("cache_key", "value", "expires") VALUES (%s, %s, %s) '
                        'ON CONFLICT ("cache_key") DO UPDATE '
                        'SET "value" = EXCLUDED."value", "expires" = EXCLUDED."expires" '
                        'WHERE entry."expires" < %s RETURNING "cache_key"',
                        [
                            key,
                            encoded,
                            connection.ops.adapt_datetimefield_value(expires.replace(microsecond=0)),
                            connection.ops.adapt_datetimefield_value(now),
                        ],
                    )
                    return cursor.fetchone() is not None
            except DatabaseError:
                return False

    def incr(self, key, delta=1, version=None):
        key = self.make_and_validate_key(key, version)
        alias = router.db_for_write(self.cache_model_class)
        connection = connections[alias]
        table = connection.ops.quote_name(self._table)
        with transaction.atomic(using=alias), connection.cursor() as cursor:
            cursor.execute(f'SELECT "value", "expires" FROM {table} WHERE "cache_key" = %s FOR UPDATE', [key])
            row = cursor.fetchone()
            if row is None or row[1] <= timezone.now():
                raise ValueError("Cache key expired or missing")
            value = pickle.loads(base64.b64decode(row[0])) + delta
            encoded = base64.b64encode(pickle.dumps(value, self.pickle_protocol)).decode("ascii")
            cursor.execute(f'UPDATE {table} SET "value" = %s WHERE "cache_key" = %s', [encoded, key])
            return value
