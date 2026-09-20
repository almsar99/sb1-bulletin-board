"""Уведомление после успешного сохранения публикации."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_publication_email(*, email, title, ad_id):
    try:
        context = {"title": title, "ad_url": f'{settings.FRONTEND_URL.rstrip("/")}/ad/{ad_id}/'}
        send_mail(
            "Объявление опубликовано — маркет.код",
            render_to_string("emails/ad_published.txt", context),
            settings.DEFAULT_FROM_EMAIL,
            [email],
            html_message=render_to_string("emails/ad_published.html", context),
        )
    except Exception:
        logger.exception("Не удалось отправить письмо о публикации объявления %s", ad_id)
