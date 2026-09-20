"""Общие правила регистрации для API и HTML-форм."""

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError

from users.models import User


def validate_registration_email(value):
    email = value.lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError("Учётная запись с таким адресом уже существует")
    return email


def validate_registration_phone(value):
    if value and User.objects.filter(phone=value).exists():
        raise ValidationError("Учётная запись с таким телефоном уже существует")
    return value


def validate_registration_password(value):
    password_validation.validate_password(value)
    return value


def validate_password_confirmation(password, confirmation):
    if password != confirmation:
        raise ValidationError({"password_confirm": "Пароли не совпадают"})
