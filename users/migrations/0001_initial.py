import django.core.validators
from django.db import migrations, models

import users.managers


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(blank=True, null=True, verbose_name="last login"),
                ),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Designates that this user has all permissions without "
                            "explicitly assigning them."
                        ),
                        verbose_name="superuser status",
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        max_length=254,
                        unique=True,
                        verbose_name="адрес электронной почты",
                    ),
                ),
                (
                    "first_name",
                    models.CharField(
                        blank=True,
                        max_length=150,
                        verbose_name="имя",
                    ),
                ),
                (
                    "last_name",
                    models.CharField(
                        blank=True,
                        max_length=150,
                        verbose_name="фамилия",
                    ),
                ),
                (
                    "phone",
                    models.CharField(
                        blank=True,
                        max_length=16,
                        validators=[
                            django.core.validators.RegexValidator(
                                message=(
                                    "Телефон указывается цифрами, допускается "
                                    "ведущий знак «плюс»"
                                ),
                                regex="^\\+?\\d{10,15}$",
                            )
                        ],
                        verbose_name="телефон",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("user", "Пользователь"),
                            ("admin", "Администратор"),
                        ],
                        default="user",
                        max_length=16,
                        verbose_name="роль",
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="users/",
                        verbose_name="изображение профиля",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        verbose_name="активна",
                    ),
                ),
                (
                    "is_staff",
                    models.BooleanField(
                        default=False,
                        verbose_name="доступ к панели управления",
                    ),
                ),
                (
                    "date_joined",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="дата регистрации",
                    ),
                ),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text=(
                            "The groups this user belongs to. A user will get all permissions "
                            "granted to each of their groups."
                        ),
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
            ],
            options={
                "verbose_name": "пользователь",
                "verbose_name_plural": "пользователи",
                "ordering": ["-date_joined"],
            },
            managers=[
                ("objects", users.managers.UserManager()),
            ],
        ),
    ]
