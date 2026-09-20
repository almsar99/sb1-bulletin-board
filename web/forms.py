"""Формы страниц; изменения сохраняются через ORM."""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm

from ads.discussions import validate_discussion
from ads.models import Ad, Review
from users.models import SignupRequest
from users.reset import request_password_reset
from users.signup import submit_signup_request
from users.validation import (
    validate_password_confirmation,
    validate_registration_email,
    validate_registration_password,
    validate_registration_phone,
)


class AdForm(forms.ModelForm):
    class Meta:
        model = Ad
        fields = ("title", "price", "description", "category", "image")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Например, скрипт для обработки данных"}),
            "price": forms.NumberInput(attrs={"min": 0, "step": 1, "placeholder": "0"}),
            "description": forms.Textarea(
                attrs={"rows": 6, "placeholder": "Расскажите о предложении, условиях и особенностях"}
            ),
            "image": forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp"}),
        }
        help_texts = {
            "image": "JPEG, PNG или WebP, не больше 5 МБ. Фотография необязательна.",
            "price": "В рублях, без копеек. Укажите 0, если отдаёте даром.",
        }


class ReviewForm(forms.ModelForm):
    def __init__(self, *args, ad=None, kind="review", **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.ad = ad
        self.instance.kind = kind
        self.fields["parent"].queryset = Review.objects.filter(ad=ad, kind="question", parent__isnull=True)
        self.fields["text"].label = "Ваш вопрос" if kind == "question" else "Ваш отзыв"

    def clean(self):
        data = super().clean()
        validate_discussion(
            ad_id=self.instance.ad_id, kind=self.instance.kind, parent=data.get("parent"), instance=self.instance
        )
        return data

    class Meta:
        model = Review
        fields = ("text", "parent")
        labels = {"text": "Ваш отзыв"}
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3, "placeholder": "Напишите сообщение"}),
            "parent": forms.HiddenInput(),
        }


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Электронная почта", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"})
    )

    def full_clean(self):
        super().full_clean()
        if self._errors:
            self._errors.clear()
            self.add_error(None, "Неверная почта или пароль.")

    def clean_username(self):
        return self.cleaned_data["username"].lower()


class SignupForm(forms.ModelForm):
    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Не менее 8 символов. Не используйте распространённые пароли или только цифры.",
    )
    password_confirm = forms.CharField(
        label="Подтверждение пароля", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )

    class Meta:
        model = SignupRequest
        fields = ("email", "first_name", "last_name", "phone")
        widgets = {
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "first_name": forms.TextInput(attrs={"autocomplete": "given-name"}),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel", "type": "tel"}),
        }

    def clean_email(self):
        return validate_registration_email(self.cleaned_data["email"])

    def clean_password(self):
        return validate_registration_password(self.cleaned_data["password"])

    def clean_phone(self):
        return validate_registration_phone(self.cleaned_data["phone"])

    def validate_unique(self):
        # Совпадение с заявкой допустимо: сервис заменит её в транзакции.
        pass

    def clean(self):
        data = super().clean()
        if "password" in data and "password_confirm" in data:
            validate_password_confirmation(data["password"], data["password_confirm"])
        return data

    def save(self):
        return submit_signup_request(**{name: self.cleaned_data[name] for name in (*self.Meta.fields, "password")})


class SitePasswordResetForm(PasswordResetForm):
    def save(self, **kwargs):
        request_password_reset(self.cleaned_data["email"])
