"""Проверка JWT относительно последней смены пароля."""

from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer, TokenVerifySerializer
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken

from users.models import User

ISSUED_AT_US = "issued_at_us"
PASSWORD_CHANGED_AT = "password_changed_at"


def password_stamp(user):
    return user.password_changed_at.isoformat() if user.password_changed_at else None


def check_password_timestamp(user, token):
    if user.password_changed_at is None:
        return
    issued_at = token.get(ISSUED_AT_US)
    if issued_at is None:
        iat = token.get("iat")
        issued_at = iat * 1_000_000 if type(iat) is int else None
    changed_at = int(user.password_changed_at.timestamp() * 1_000_000)
    if (
        type(issued_at) is not int
        or issued_at < changed_at
        or PASSWORD_CHANGED_AT in token
        and token[PASSWORD_CHANGED_AT] != password_stamp(user)
    ):
        raise InvalidToken("Недействительный токен.")


class PasswordAwareRefreshToken(RefreshToken):
    @classmethod
    def for_user(cls, user):
        token = super().for_user(user)
        # Стандартный iat округлён до секунд; сохраняем точность и состояние при входе.
        token[ISSUED_AT_US] = int(token.current_time.timestamp() * 1_000_000)
        token[PASSWORD_CHANGED_AT] = password_stamp(user)
        return token


class PasswordAwareJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        check_password_timestamp(user, validated_token)
        return user


class PasswordAwareRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        refresh = self.token_class(attrs["refresh"])
        user = PasswordAwareJWTAuthentication().get_user(refresh)
        # При обновлении старого токена сохраняем исходное время и проверенное состояние.
        # Смена пароля между проверкой и выдачей также должна отозвать новый access.
        if ISSUED_AT_US not in refresh:
            refresh[ISSUED_AT_US] = refresh.get("iat", 0) * 1_000_000
        refresh[PASSWORD_CHANGED_AT] = password_stamp(user)
        try:
            return super().validate({**attrs, "refresh": str(refresh)})
        except User.DoesNotExist as error:
            # SimpleJWT повторно читает пользователя после нашей проверки.
            # Удаление между этими чтениями также означает отказ в обновлении.
            raise InvalidToken("Недействительный токен.") from error


class PasswordAwareVerifySerializer(TokenVerifySerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        PasswordAwareJWTAuthentication().get_user(UntypedToken(attrs["token"]))
        return data


class PasswordAwareJWTScheme(SimpleJWTScheme):
    target_class = "users.authentication.PasswordAwareJWTAuthentication"
