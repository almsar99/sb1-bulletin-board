"""Окна фиксированной длины; последовательность отправок обеспечивается блокировкой строки отправителя."""

from django.core.cache import cache

ERROR = "Слишком часто. Подождите минуту."


def allow_send(user_id, *, new_thread=False):
    limits = [(f"messaging:send:{user_id}", 10, 60)]
    if new_thread:
        limits.append((f"messaging:thread:{user_id}", 1, 30))
    if any(cache.get(key, 0) >= limit for key, limit, _ in limits):
        return False
    for key, _, window in limits:
        if not cache.add(key, 1, timeout=window):
            try:
                cache.incr(key)
            except ValueError:
                # Срок окна может истечь между add и incr.
                cache.add(key, 1, timeout=window)
    return True
