"""Панель управления учётными записями."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm
from django.utils import timezone

from users.models import User


class UserAdminPasswordChangeForm(AdminPasswordChangeForm):
    """Смена пароля без перезаписи остальных данных устаревшей копией пользователя."""

    def save(self, commit=True):
        user = super().save(commit=False)
        if not user.has_usable_password():
            # set_unusable_password() не устанавливает _password для User.save().
            user.password_changed_at = timezone.now()
        if commit:
            user.save(update_fields=["password", "password_changed_at"])
        return user


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    change_password_form = UserAdminPasswordChangeForm
    ordering = ("-date_joined",)
    list_display = ("email", "first_name", "last_name", "role", "is_active", "date_joined")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("email", "first_name", "last_name", "phone")
    readonly_fields = ("date_joined", "last_login")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Личные данные", {"fields": ("first_name", "last_name", "phone", "image")}),
        ("Права", {"fields": ("role", "is_active", "is_staff", "is_superuser", "groups")}),
        ("Даты", {"fields": ("date_joined", "last_login")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "role", "is_staff", "is_superuser"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            return super().save_model(request, obj, form, change)
        concrete_fields = {field.name for field in obj._meta.concrete_fields if not field.primary_key}
        update_fields = concrete_fields.intersection(form.changed_data)
        if update_fields:
            obj.save(update_fields=update_fields)
