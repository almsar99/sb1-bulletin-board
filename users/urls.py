"""Маршруты учётных записей."""

from django.urls import path

from users.views import (
    LoginView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    ProfileView,
    RefreshView,
    RegistrationView,
    SignupResendView,
    VerifyView,
)

app_name = "users"

urlpatterns = [
    path("reset_password/", PasswordResetRequestView.as_view(), name="password-reset"),
    path("reset_password_confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path(
        "signup/resend/",
        SignupResendView.as_view(),
        name="signup-resend",
    ),
    path("register/", RegistrationView.as_view(), name="register"),
    path("token/", LoginView.as_view(), name="token-obtain"),
    path("token/refresh/", RefreshView.as_view(), name="token-refresh"),
    path("token/verify/", VerifyView.as_view(), name="token-verify"),
    path("me/", ProfileView.as_view(), name="profile"),
    path("set_password/", PasswordChangeView.as_view(), name="password-change"),
]
