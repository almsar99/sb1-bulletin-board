"""Создание пользователей по адресу электронной почты."""

from django.contrib.auth.models import BaseUserManager
from django.utils import timezone


class UserManager(BaseUserManager):
    """Менеджер модели пользователя: логином служит адрес электронной почты."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        return (email or "").strip().lower()

    def get_by_natural_key(self, username):
        try:
            return self.get(email__iexact=self.normalize_email(username))
        except self.model.MultipleObjectsReturned as error:
            # Старые адреса могли различаться только регистром. Не выбираем
            # произвольную учётную запись при неоднозначном логине.
            raise self.model.DoesNotExist from error

    async def aget_by_natural_key(self, username):
        try:
            return await self.aget(email__iexact=self.normalize_email(username))
        except self.model.MultipleObjectsReturned as error:
            raise self.model.DoesNotExist from error

    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        if not email:
            raise ValueError("Адрес электронной почты обязателен")
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        from users.models import UserRole

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", UserRole.ADMIN)
        extra_fields.setdefault("email_verified_at", timezone.now())

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Администратор должен иметь признак is_staff")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Администратор должен иметь признак is_superuser")
        return self._create_user(email, password, **extra_fields)
