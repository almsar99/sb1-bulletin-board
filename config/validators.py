"""Общая проверка загружаемых изображений профиля и объявления."""

from pathlib import Path

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_SIZE = 5 * 1024 * 1024
IMAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


def validate_image(value):
    if value.size > MAX_IMAGE_SIZE:
        raise ValidationError("Размер фотографии не должен превышать 5 МБ.", code="image_too_large")
    extension = Path(value.name).suffix.lower()
    if extension not in IMAGE_FORMATS:
        raise ValidationError("Допустимы фотографии JPEG, PNG и WebP.", code="image_extension")
    position = value.tell()
    try:
        value.seek(0)
        with Image.open(value) as image:
            if image.format != IMAGE_FORMATS[extension]:
                raise ValidationError("Содержимое фотографии не соответствует расширению файла.", code="image_format")
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValidationError(
            "Не удалось прочитать фотографию. Загрузите корректное изображение.", code="invalid_image"
        ) from exc
    finally:
        value.seek(position)
