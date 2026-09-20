"""Одноразовые ссылки учётных записей."""

from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from users.models import User


def encode_user_uid(user):
    return urlsafe_base64_encode(force_bytes(user.pk))


def decode_user_uid(uid):
    try:
        pk = int(urlsafe_base64_decode(uid).decode())
        if not 0 < pk < 2**63:
            return None
        return pk
    except ValueError, TypeError, UnicodeDecodeError, OverflowError:
        return None


def make_reset_credentials(user):
    return encode_user_uid(user), default_token_generator.make_token(user)


def get_reset_user(uid):
    pk = decode_user_uid(uid)
    if pk is None:
        return None
    return User.objects.filter(pk=pk, is_active=True).first()
