import django.core.validators
from django.db import migrations, models

import users.models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0003_user_email_verified_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignupRequest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "email",
                    models.EmailField(max_length=254, unique=True, verbose_name="адрес электронной почты"),
                ),
                ("first_name", models.CharField(blank=True, max_length=150, verbose_name="имя")),
                ("last_name", models.CharField(blank=True, max_length=150, verbose_name="фамилия")),
                (
                    "phone",
                    models.CharField(
                        blank=True,
                        max_length=16,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Телефон указывается цифрами, допускается ведущий знак «плюс»",
                                regex="^\\+?\\d{10,15}$",
                            )
                        ],
                        verbose_name="телефон",
                    ),
                ),
                ("password", models.CharField(max_length=128, verbose_name="хеш пароля")),
                (
                    "token",
                    models.CharField(
                        default=users.models.generate_signup_token,
                        max_length=128,
                        unique=True,
                        verbose_name="значение ссылки",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="дата создания")),
            ],
            options={
                "verbose_name": "заявка на регистрацию",
                "verbose_name_plural": "заявки на регистрацию",
            },
        ),
    ]
