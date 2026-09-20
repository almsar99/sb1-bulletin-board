#!/bin/sh
set -eu

python - <<'PY'
import os
import time

import django
from django.db import OperationalError, connections

django.setup()
timeout = int(os.environ.get("DATABASE_WAIT_TIMEOUT", "60"))
if not 1 <= timeout <= 300:
    raise SystemExit("DATABASE_WAIT_TIMEOUT must be between 1 and 300 seconds")
deadline = time.monotonic() + timeout
while True:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
        break
    except OperationalError:
        connections.close_all()
        if time.monotonic() >= deadline:
            raise SystemExit("Database did not become ready before the timeout")
        time.sleep(1)
connections.close_all()
PY

python manage.py migrate --noinput
python manage.py createcachetable
python manage.py collectstatic --noinput
exec "$@"
