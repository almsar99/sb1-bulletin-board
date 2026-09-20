"""Совместимый путь валидатора фотографий для модели и существующих миграций."""

from config.validators import MAX_IMAGE_SIZE, validate_image

__all__ = ["MAX_IMAGE_SIZE", "validate_ad_image"]


def validate_ad_image(value):
    validate_image(value)
