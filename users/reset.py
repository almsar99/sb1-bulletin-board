"""Запрос письма восстановления с общей для сайта и API паузой."""

from users.email import send_password_reset_email
from users.models import User
from users.rate_limit import acquire_email_send


def request_password_reset(email):
    email = email.strip().lower()
    wait = acquire_email_send("reset", email)
    if wait:
        return wait
    # Пауза одинакова и для неизвестного адреса: ответ не раскрывает аккаунт.
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user and user.has_usable_password():
        send_password_reset_email(user)
    return 0
