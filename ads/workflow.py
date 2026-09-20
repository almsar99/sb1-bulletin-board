"""Создание и изменение объявлений."""

from functools import partial

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from ads.email import send_publication_email
from ads.models import Ad, AdStatus


def create_ad(*, author, data, via_web=False):
    data = {key: "" if key == "image" and value is False else value for key, value in data.items()}
    ad = Ad(author=author, **data)
    if via_web:
        ad.status = AdStatus.DRAFT
    if via_web or not author.is_admin or ad.status == AdStatus.DRAFT:
        ad.submitted_at = timezone.now()
    ad.save()
    return ad


@transaction.atomic
def update_ad(*, pk, actor, changes, status_action=False):
    ad = get_object_or_404(Ad.objects.select_for_update().select_related("author"), pk=pk)
    if not actor.is_admin and actor.pk != ad.author_id:
        raise PermissionDenied
    if not changes:
        return ad
    old_status = ad.status
    target = changes.get("status", old_status)
    if target not in AdStatus.values:
        raise ValidationError({"status": "Неизвестное состояние."})
    status_only = status_action or set(changes) == {"status"}
    if status_only:
        if not actor.is_admin and not (old_status == AdStatus.PUBLISHED and target == AdStatus.ARCHIVED):
            raise PermissionDenied("Изменить это состояние может только администратор.")
        ad.status = target
        if target == AdStatus.DRAFT and old_status != target:
            ad.submitted_at = timezone.now()
    else:
        if not actor.is_admin and target != old_status and target != AdStatus.ARCHIVED:
            raise PermissionDenied("Опубликовать или вернуть на рассмотрение может только администратор.")
        for field, value in changes.items():
            setattr(ad, field, "" if field == "image" and value is False else value)
        if not actor.is_admin:
            ad.status = AdStatus.DRAFT
            ad.submitted_at = timezone.now()
        elif ad.status == AdStatus.DRAFT and old_status != ad.status:
            ad.submitted_at = timezone.now()
    ad.save()
    if actor.is_admin and old_status != AdStatus.PUBLISHED and ad.status == AdStatus.PUBLISHED:
        transaction.on_commit(partial(send_publication_email, email=ad.author.email, title=ad.title, ad_id=ad.pk))
    return ad
