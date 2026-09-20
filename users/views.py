"""Обработчики учётных записей."""

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

from users.models import SignupRequest
from users.reset import request_password_reset
from users.serializers import (
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegistrationSerializer,
    SignupResendSerializer,
    UserSerializer,
)
from users.signup import resend_signup_confirmation


@extend_schema(tags=["Учётные записи"], summary="Регистрация")
class RegistrationView(generics.CreateAPIView):
    """Создание заявки на регистрацию. Доступно без авторизации."""

    queryset = SignupRequest.objects.all()
    serializer_class = RegistrationSerializer
    permission_classes = [AllowAny]


@extend_schema(tags=["Учётные записи"], summary="Профиль текущего пользователя")
class ProfileView(generics.RetrieveUpdateAPIView):
    """Чтение и изменение собственных данных."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


@extend_schema(tags=["Учётные записи"], summary="Смена пароля", responses={204: None})
class PasswordChangeView(APIView):
    """Смена пароля владельцем учётной записи."""

    permission_classes = [IsAuthenticated]
    serializer_class = PasswordChangeSerializer

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Учётные записи"], summary="Запрос восстановления пароля", responses={204: None})
class PasswordResetRequestView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PasswordResetRequestSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request_password_reset(serializer.validated_data["email"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Учётные записи"], summary="Подтверждение восстановления пароля", responses={204: None})
class PasswordResetConfirmView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(exclude=True)
class PasswordResetRequestAliasView(PasswordResetRequestView):
    pass


@extend_schema(exclude=True)
class PasswordResetConfirmAliasView(PasswordResetConfirmView):
    pass


@extend_schema(tags=["Учётные записи"], summary="Повторить письмо регистрации", responses={204: None})
class SignupResendView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = SignupResendSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        resend_signup_confirmation(serializer.validated_data["email"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Учётные записи"], summary="Вход по электронной почте")
class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer


@extend_schema(tags=["Учётные записи"], summary="Обновить токен доступа")
class RefreshView(TokenRefreshView):
    pass


@extend_schema(tags=["Учётные записи"], summary="Проверить токен")
class VerifyView(TokenVerifyView):
    pass
