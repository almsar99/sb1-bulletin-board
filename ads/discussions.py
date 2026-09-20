"""Общие правила вопросов и ответов для форм и API."""

from django.core.exceptions import ValidationError

from ads.models import DiscussionKind, Review


def validate_discussion(*, ad_id, kind, parent, instance=None):
    if parent and (parent.ad_id != ad_id or parent.kind != DiscussionKind.QUESTION or parent.parent_id):
        raise ValidationError({"parent": "Ответить можно на вопрос этого объявления."})
    if parent and instance and parent.pk == instance.pk:
        raise ValidationError({"parent": "Запись не может быть ответом самой себе."})
    if (
        instance
        and instance.pk
        and (kind != DiscussionKind.QUESTION or parent)
        and Review.objects.filter(parent_id=instance.pk).exists()
    ):
        raise ValidationError({"kind": "Вопрос с ответами нельзя превращать в отзыв или ответ."})
