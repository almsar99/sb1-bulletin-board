"""Схемы обмена данными учётных записей."""

from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from users.authentication import PasswordAwareRefreshToken
from users.models import SignupRequest, User
from users.signup import EmailSendCooldown, submit_signup_request
from users.validation import (
    validate_password_confirmation,
    validate_registration_email,
    validate_registration_password,
    validate_registration_phone,
)


class LoginSerializer(TokenObtainPairSerializer):
    token_class = PasswordAwareRefreshToken

    default_error_messages = {"no_active_account": "Неверная почта или пароль."}

    def validate(self, attrs):
        return super().validate({**attrs, "email": attrs["email"].lower()})


class UserSerializer(serializers.ModelSerializer):
    """Профиль пользователя с сохранением только переданных редактируемых полей."""

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "image",
            "date_joined",
        )
        read_only_fields = ("id", "email", "role", "date_joined")

    def update(self, instance, validated_data):
        # request.user мог быть загружен до параллельной смены пароля или прав.
        # Полное save() восстановило бы эти поля из устаревшего экземпляра.
        for field, value in validated_data.items():
            setattr(instance, field, value)
        try:
            if validated_data:
                instance.save(update_fields=validated_data.keys())
            instance.refresh_from_db()
        except (User.NotUpdated, User.DoesNotExist) as error:
            # Аккаунт могли удалить после аутентификации или сразу после записи.
            raise AuthenticationFailed("Учётная запись недоступна.") from error
        return instance


class RegistrationSerializer(serializers.ModelSerializer):
    """Заявка на регистрацию; учётная запись появится после подтверждения почты."""

    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = SignupRequest
        fields = (
            "email",
            "first_name",
            "last_name",
            "phone",
            "password",
            "password_confirm",
        )
        # Повторная заявка заменяет предыдущую, уникальность проверяется при сохранении.
        extra_kwargs = {"email": {"validators": []}}

    def validate_email(self, value):
        return validate_registration_email(value)

    def validate_password(self, value):
        return validate_registration_password(value)

    def validate_phone(self, value):
        return validate_registration_phone(value)

    def validate(self, attrs):
        validate_password_confirmation(attrs["password"], attrs["password_confirm"])
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        try:
            return submit_signup_request(**validated_data)
        except EmailSendCooldown as error:
            raise Throttled(wait=error.retry_after, detail=error.message) from error
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error


class PasswordChangeSerializer(serializers.Serializer):
    """Смена пароля владельцем учётной записи."""

    current_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Текущий пароль указан неверно")
        return value

    def validate_new_password(self, value):
        password_validation.validate_password(value, self.context["request"].user)
        return value

    def save(self, **kwargs):
        from django.db import transaction

        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=self.context["request"].user.pk).first()
            if user is None or not user.is_active:
                raise AuthenticationFailed("Учётная запись недоступна.")
            if not user.check_password(self.validated_data["current_password"]):
                raise serializers.ValidationError({"current_password": "Текущий пароль указан неверно"})
            user.set_password(self.validated_data["new_password"])
            user.save(update_fields=["password"])
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class SignupResendSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=128)
    token = serializers.CharField(max_length=128)
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        from django.contrib.auth.tokens import default_token_generator

        from users.tokens import get_reset_user

        user = get_reset_user(attrs["uid"])
        if (
            user is None
            or not user.has_usable_password()
            or not default_token_generator.check_token(user, attrs["token"])
        ):
            raise serializers.ValidationError("Ссылка недействительна или истекла")
        password_validation.validate_password(attrs["new_password"], user)
        return attrs

    def save(self, **kwargs):
        from django.contrib.auth.tokens import default_token_generator
        from django.db import transaction

        from users.tokens import get_reset_user

        with transaction.atomic():
            user = get_reset_user(self.validated_data["uid"])
            if user is None:
                raise serializers.ValidationError("Ссылка недействительна или истекла")
            user = User.objects.select_for_update().filter(pk=user.pk).first()
            if (
                user is None
                or not user.is_active
                or not default_token_generator.check_token(user, self.validated_data["token"])
            ):
                raise serializers.ValidationError("Ссылка недействительна или истекла")
            user.set_password(self.validated_data["new_password"])
            user.save(update_fields=["password"])
        return user
