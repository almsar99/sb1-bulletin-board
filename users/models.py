"""Учётные записи и заявки на регистрацию."""

import secrets
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from config.validators import validate_image
from users.managers import UserManager
from users.passwords import check_user_password

phone_validator = RegexValidator(
    regex=r"^\+?\d{10,15}$",
    message="Телефон указывается цифрами, допускается ведущий знак «плюс»",
)

SIGNUP_REQUEST_TTL = timedelta(days=1)


def generate_signup_token():
    return secrets.token_urlsafe(32)


class UserRole(models.TextChoices):
    USER = "user", "Пользователь"
    ADMIN = "admin", "Администратор"


class User(AbstractBaseUser, PermissionsMixin):
    """Учётная запись. Входом служит адрес электронной почты."""

    email = models.EmailField("адрес электронной почты", unique=True)
    first_name = models.CharField("имя", max_length=150, blank=True)
    last_name = models.CharField("фамилия", max_length=150, blank=True)
    phone = models.CharField(
        "телефон",
        max_length=16,
        blank=True,
        validators=[phone_validator],
    )
    role = models.CharField(
        "роль",
        max_length=16,
        choices=UserRole.choices,
        default=UserRole.USER,
    )
    image = models.ImageField(
        "изображение профиля",
        upload_to="users/",
        blank=True,
        null=True,
        validators=[validate_image],
    )
    is_active = models.BooleanField("активна", default=True)
    is_staff = models.BooleanField("доступ к панели управления", default=False)
    date_joined = models.DateTimeField("дата регистрации", auto_now_add=True)

    password_changed_at = models.DateTimeField(
        "дата смены пароля",
        null=True,
        blank=True,
    )
    email_verified_at = models.DateTimeField(
        "дата подтверждения почты",
        null=True,
        blank=True,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"
        ordering = ["-date_joined"]

    def check_password(self, raw_password):
        return check_user_password(self, raw_password)

    async def acheck_password(self, raw_password):
        return await sync_to_async(self.check_password)(raw_password)

    def save(self, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is None or "email" in update_fields:
            self.email = UserManager.normalize_email(self.email)
            self._validate_email_unique(using=kwargs.get("using") or self._state.db)
        # Обновление хеша при входе пишет только password через сравнение в БД.
        if (
            not self._state.adding
            and self._password is not None
            and (update_fields is None or "password" in update_fields)
        ):
            self.password_changed_at = timezone.now()
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "password_changed_at"}
        super().save(**kwargs)

    def clean(self):
        super().clean()
        self.email = UserManager.normalize_email(self.email)

    def _validate_email_unique(self, *, using=None):
        accounts = type(self).objects.using(using).filter(email__iexact=UserManager.normalize_email(self.email))
        if not self._state.adding:
            accounts = accounts.exclude(pk=self.pk)
        if accounts.exists():
            raise ValidationError({"email": self.unique_error_message(type(self), ["email"])})

    def validate_unique(self, exclude=None):
        excluded_fields = set(exclude or ())
        super().validate_unique(exclude=excluded_fields | {"email"})
        if "email" not in excluded_fields:
            self._validate_email_unique(using=self._state.db)

    def __str__(self):
        return self.email

    @property
    def is_admin(self):
        """Учётная запись с ролью администратора."""
        return self.role == UserRole.ADMIN

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email

    def get_short_name(self):
        return self.first_name or self.email


class SignupRequest(models.Model):
    """Данные регистрации до подтверждения адреса электронной почты."""

    email = models.EmailField("адрес электронной почты", unique=True)
    first_name = models.CharField("имя", max_length=150, blank=True)
    last_name = models.CharField("фамилия", max_length=150, blank=True)
    phone = models.CharField("телефон", max_length=16, blank=True, validators=[phone_validator])
    password = models.CharField("хеш пароля", max_length=128)
    token = models.CharField("значение ссылки", max_length=128, unique=True, default=generate_signup_token)
    created_at = models.DateTimeField("дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "заявка на регистрацию"
        verbose_name_plural = "заявки на регистрацию"

    def save(self, **kwargs):
        self.email = self.email.lower()
        super().save(**kwargs)

    @property
    def is_expired(self):
        return timezone.now() >= self.created_at + SIGNUP_REQUEST_TTL
