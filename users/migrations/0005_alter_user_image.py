import config.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_signup_request"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="image",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="users/",
                validators=[config.validators.validate_image],
                verbose_name="изображение профиля",
            ),
        ),
    ]
