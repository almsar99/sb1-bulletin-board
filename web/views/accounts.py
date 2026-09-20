"""Личный кабинет, вход, регистрация и восстановление пароля."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    INTERNAL_RESET_SESSION_TOKEN,
    LoginView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from ads.models import AdStatus
from users.models import User
from users.rate_limit import email_send_wait
from users.reset import request_password_reset
from users.signup import EmailSendCooldown, confirm_signup_request, resend_signup_confirmation
from users.tokens import get_reset_user
from web.forms import LoginForm, SignupForm, SitePasswordResetForm
from web.views.ads import AdListView


class AccountView(LoginRequiredMixin, AdListView):
    template_name = "accounts/account.html"
    catalog_only = False

    def get_queryset(self):
        queryset = super().get_queryset().filter(author=self.request.user)
        status = self.request.GET.get("status")
        return queryset.filter(status=status) if status in AdStatus.values else queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status = self.request.GET.get("status")
        context["active_status"] = status if status in AdStatus.values else ""
        return context


class SiteLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm


@sensitive_post_parameters("password", "password_confirm")
@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("web:ad-list")
    form = SignupForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            signup_request = form.save()
        except EmailSendCooldown:
            request.session["verification_email"] = form.cleaned_data["email"].strip().lower()
            return redirect("web:signup-check-email")
        except ValidationError as error:
            form.add_error(None, error)
        else:
            request.session["verification_email"] = signup_request.email
            return redirect("web:signup-check-email")
    return render(request, "accounts/signup.html", {"form": form})


@never_cache
@require_http_methods(["GET"])
def signup_check_email(request):
    email = request.session.get("verification_email", "")
    return render(
        request,
        "accounts/signup_check_email.html",
        {"verification_email": email, "email_retry_after": email_send_wait("signup", email) if email else 0},
    )


@require_POST
def resend_verification_email(request):
    resend_signup_confirmation(request.session.get("verification_email", ""))

    return redirect("web:signup-check-email")


@require_http_methods(["GET"])
def confirm_signup(request, token):
    user = confirm_signup_request(token)

    return render(
        request,
        "accounts/signup_confirmation_result.html",
        {"verified": user is not None},
    )


class SitePasswordResetView(PasswordResetView):
    form_class = SitePasswordResetForm
    template_name = "accounts/password_reset_form.html"
    success_url = reverse_lazy("web:password-reset-done")

    def form_valid(self, form):
        self.request.session["reset_email"] = form.cleaned_data["email"].strip().lower()
        return super().form_valid(form)


@method_decorator(never_cache, name="dispatch")
class SitePasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        email = self.request.session.get("reset_email", "")
        context["reset_email"] = email
        context["email_retry_after"] = email_send_wait("reset", email) if email else 0
        return context


@require_POST
def resend_password_reset_email(request):
    email = request.session.get("reset_email", "")
    if not email:
        return redirect("web:password-reset")
    request_password_reset(email)
    return redirect("web:password-reset-done")


class SitePasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("web:password-reset-complete")

    def get_user(self, uidb64):
        return get_reset_user(uidb64)

    def form_valid(self, form):
        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=self.user.pk, is_active=True).first()
            token = self.request.session.get(INTERNAL_RESET_SESSION_TOKEN)
            if user is None or not self.token_generator.check_token(user, token):
                self.validlink = False
                return self.render_to_response(self.get_context_data())
            form.user = user
            return super().form_valid(form)
