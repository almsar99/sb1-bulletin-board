"""Удаление заявок на регистрацию с истёкшим сроком действия."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from users.models import SIGNUP_REQUEST_TTL, SignupRequest


class Command(BaseCommand):
    help = "Удалить просроченные заявки на регистрацию"

    def handle(self, *args, **options):
        _, deleted_by_model = SignupRequest.objects.filter(
            created_at__lte=timezone.now() - SIGNUP_REQUEST_TTL
        ).delete()
        count = deleted_by_model.get(SignupRequest._meta.label, 0)
        self.stdout.write(f"Удалено заявок: {count}")
