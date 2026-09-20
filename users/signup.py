"""Регистрация с подтверждением почты."""

import re

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from users.email import send_signup_confirmation_email
from users.models import SignupRequest, User
from users.rate_limit import acquire_email_send, allow_signup_resend
from users.validation import (
    validate_registration_email,
    validate_registration_password,
    validate_registration_phone,
)


class EmailSendCooldown(ValidationError):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__("Повторное письмо можно отправить через минуту.", code="email_send_cooldown")


def _validate_available_fields(email, phone):
    errors = {}
    for field, value, validator in (
        ("email", email, validate_registration_email),
        ("phone", phone, validate_registration_phone),
    ):
        try:
            validator(value)
        except ValidationError as error:
            errors[field] = error.messages
    if errors:
        raise ValidationError(errors)


def _signup_request_fields(*, email, password, first_name="", last_name="", phone=""):
    email = email.strip().lower()
    _validate_available_fields(email, phone)
    try:
        validate_registration_password(password)
    except ValidationError as error:
        raise ValidationError({"password": error.messages}) from error
    fields = {
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone,
        "password": make_password(password),
    }
    SignupRequest(**fields).full_clean(validate_unique=False)
    return fields


def _replace_signup_request(fields):
    email, phone = fields["email"], fields["phone"]

    # Если заявки ещё нет, блокировать нечего. Уникальность email разрешает
    # состязание первых регистраций; проигравший повторяет замену в новой транзакции.
    for _ in range(3):
        try:
            with transaction.atomic():
                list(SignupRequest.objects.select_for_update().filter(email=email))
                _validate_available_fields(email, phone)
                SignupRequest.objects.filter(email=email).delete()
                return SignupRequest.objects.create(**fields)
        except IntegrityError:
            continue
    raise ValidationError({"email": "Не удалось обновить заявку. Повторите регистрацию."})


def submit_signup_request(*, email, password, first_name="", last_name="", phone=""):
    """Создать заявку и отправить первое письмо с общей паузой повторной отправки."""
    fields = _signup_request_fields(
        email=email, password=password, first_name=first_name, last_name=last_name, phone=phone
    )
    wait = acquire_email_send("signup", fields["email"])
    if wait:
        raise EmailSendCooldown(wait)
    signup_request = _replace_signup_request(fields)
    send_signup_confirmation_email(signup_request)
    return signup_request


def confirm_signup_request(token):
    """Создать пользователя один раз или вернуть None для недействительной ссылки."""
    if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        return None
    try:
        with transaction.atomic():
            signup_request = SignupRequest.objects.select_for_update().filter(token=token).first()
            if signup_request is None or signup_request.is_expired:
                return None
            _validate_available_fields(signup_request.email, signup_request.phone)
            user = User.objects.create(
                email=signup_request.email,
                first_name=signup_request.first_name,
                last_name=signup_request.last_name,
                phone=signup_request.phone,
                # Это уже хеш Django: повторное хеширование изменило бы пароль.
                password=signup_request.password,
                email_verified_at=timezone.now(),
            )
            signup_request.delete()
            return user
    except IntegrityError, ValidationError:
        # В том числе конфликт с учётной записью, появившейся после отправки письма.
        # Откат сохраняет заявку и не допускает частично созданного пользователя.
        return None


def resend_signup_confirmation(email):
    """Повторить действующее письмо с общей паузой для API и сайта."""
    signup_request = SignupRequest.objects.filter(email__iexact=email).first()
    if (
        signup_request is not None
        and not signup_request.is_expired
        and not User.objects.filter(email__iexact=email).exists()
        and allow_signup_resend(signup_request.email)
    ):
        send_signup_confirmation_email(signup_request)
