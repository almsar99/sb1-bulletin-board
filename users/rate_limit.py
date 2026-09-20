"""Общие окна запросов и пауза между письмами учётных записей."""

import hashlib
import math
import time

from django.core.cache import cache

EMAIL_SEND_TIMEOUT = 60


def identity_key(value):
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def request_limit_wait(scope, identity, limit, window):
    now = time.time()
    wait = max(1, math.ceil(window - now % window))
    key = f"users:requests:{scope}:{int(now // window)}:{identity_key(identity)}"
    for _ in range(2):
        if cache.add(key, 1, timeout=wait + 1):
            count = 1
            break
        try:
            count = cache.incr(key)
            break
        except ValueError:
            # Ключ мог истечь между add и incr; один раз пробуем заново.
            continue
    else:
        return wait
    return wait if count > limit else 0


def email_send_wait(purpose, email):
    """Оставшаяся пауза для адреса; чтение не продлевает срок отправки."""
    getter = getattr(cache, "get_deadline", cache.get)
    deadline = getter(f"users:email-send:{purpose}:{identity_key(email)}")
    return max(0, math.ceil(deadline - time.time())) if deadline is not None else 0


def acquire_email_send(purpose, email):
    """Зарезервировать отправку атомарно либо вернуть оставшиеся секунды."""
    key = f"users:email-send:{purpose}:{identity_key(email)}"
    for _ in range(2):
        now = time.time()
        deadline = now + EMAIL_SEND_TIMEOUT
        if hasattr(cache, "add_deadline"):
            acquired = cache.add_deadline(key, deadline, now)
        else:
            acquired = cache.add(key, deadline, timeout=EMAIL_SEND_TIMEOUT)
        if acquired:
            return 0
        wait = email_send_wait(purpose, email)
        if wait:
            return wait
        # Ключ мог истечь между add и get; повторяем атомарное резервирование.
    return 1


def allow_signup_resend(email):
    return acquire_email_send("signup", email) == 0
