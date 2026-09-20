from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models import Q


class ThreadQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(Q(initiator=user) | Q(ad__author=user))


class Thread(models.Model):
    ad = models.ForeignKey("ads.Ad", on_delete=models.CASCADE, related_name="threads")
    initiator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="initiated_threads")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    objects = ThreadQuerySet.as_manager()

    class Meta:
        ordering = ["-updated_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["ad", "initiator"], name="unique_ad_initiator")]
        verbose_name = "диалог"
        verbose_name_plural = "диалоги"

    def other_participant(self, user):
        if user.pk == self.initiator_id:
            return self.ad.author
        if user.pk == self.ad.author_id:
            return self.initiator
        raise ValidationError("Недоступный диалог.")

    def clean(self):
        if self.ad_id and self.initiator_id == self.ad.author_id:
            raise ValidationError("Нельзя написать самому себе.")

    def __str__(self):
        return f"Диалог № {self.pk}: {self.ad.title}"


class Message(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="private_messages")
    text = models.TextField(max_length=2000, validators=[MaxLengthValidator(2000)])
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = "сообщение"
        verbose_name_plural = "сообщения"

    def clean(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValidationError({"text": "Введите сообщение."})
        if self.thread_id and self.author_id not in (self.thread.initiator_id, self.thread.ad.author_id):
            raise ValidationError({"author": "Автор не является участником диалога."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Сообщение № {self.pk}"
