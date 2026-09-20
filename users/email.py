"""Отправка писем учётных записей."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from users.tokens import make_reset_credentials

logger = logging.getLogger(__name__)


def _send_templated_email(template_prefix, context, recipient):
    subject = "".join(render_to_string(f"emails/{template_prefix}_subject.txt", context).splitlines())
    body = render_to_string(f"emails/{template_prefix}_body.txt", context)
    html = render_to_string(f"emails/{template_prefix}_body.html", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], html_message=html)


def send_password_reset_email(user):
    if not user.email:
        return
    try:
        uid, token = make_reset_credentials(user)
        context = {"reset_url": (f"{settings.FRONTEND_URL.rstrip('/')}" f"/password-reset/{uid}/{token}/")}
        _send_templated_email("password_reset", context, user.email)
    except Exception:
        logger.error(
            "Не удалось отправить письмо восстановления пароля пользователю %s",
            user.pk,
        )


def send_signup_confirmation_email(signup_request):
    try:
        context = {"confirmation_url": f"{settings.FRONTEND_URL.rstrip('/')}/signup/confirm/{signup_request.token}/"}
        _send_templated_email("signup_confirmation", context, signup_request.email)
    except Exception:
        logger.error(
            "Не удалось отправить письмо подтверждения заявки %s",
            signup_request.pk,
        )
