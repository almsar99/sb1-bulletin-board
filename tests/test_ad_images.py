"""Фотографии объявлений: загрузка, формат, содержимое и размер."""

from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from ads.models import Ad
from ads.validators import MAX_IMAGE_SIZE, validate_ad_image

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def image_storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def image_file(name="photo.png", image_format="PNG"):
    buffer = BytesIO()
    Image.new("RGB", (24, 16), "#9bad83").save(buffer, format=image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{image_format.lower()}")


def ad_data():
    return {"title": "Кресло", "price": 1500, "description": "Удобное кресло"}


@pytest.mark.parametrize("name,image_format", [("photo.jpg", "JPEG"), ("photo.PNG", "PNG"), ("photo.webp", "WEBP")])
def test_upload_on_create(user_client, api_client, name, image_format):
    response = user_client.post(
        "/api/ads/", {**ad_data(), "image": image_file(name, image_format)}, format="multipart"
    )
    assert response.status_code == 201
    ad = Ad.objects.get(pk=response.data["id"])
    assert ad.image.storage.exists(ad.image.name)
    assert response.data["image"].endswith(ad.image.name)
    assert ad.image.name.startswith("ads/")
    assert api_client.get("/api/ads/").data["results"][0]["image"] == response.data["image"]


def test_without_image(user_client, api_client):
    response = user_client.post("/api/ads/", ad_data())
    assert response.status_code == 201
    assert response.data["image"] is None
    assert api_client.get("/api/ads/").data["results"][0]["image"] is None


def test_oversized_image_rejected(user_client):
    source = image_file().read()
    oversized = SimpleUploadedFile("large.png", source + b"\0" * (MAX_IMAGE_SIZE + 1), content_type="image/png")
    response = user_client.post("/api/ads/", {**ad_data(), "image": oversized}, format="multipart")
    assert response.status_code == 400
    assert "5 МБ" in str(response.data["image"])
    assert not Ad.objects.exists()


def test_unsupported_format(user_client):
    response = user_client.post(
        "/api/ads/", {**ad_data(), "image": image_file("photo.gif", "GIF")}, format="multipart"
    )
    assert response.status_code == 400
    assert "JPEG, PNG и WebP" in str(response.data["image"])


@pytest.mark.parametrize("name,image_format", [("photo.jpg", "PNG"), ("photo.png", "GIF")])
def test_misleading_extension(user_client, name, image_format):
    response = user_client.post(
        "/api/ads/", {**ad_data(), "image": image_file(name, image_format)}, format="multipart"
    )
    assert response.status_code == 400
    assert "расширению" in str(response.data["image"])


def test_invalid_image_content(user_client):
    image = SimpleUploadedFile("photo.png", b"not an image", content_type="image/png")
    assert user_client.post("/api/ads/", {**ad_data(), "image": image}, format="multipart").status_code == 400


def test_validator_checks_corrupt_file_directly():
    with pytest.raises(ValidationError, match="Не удалось прочитать"):
        validate_ad_image(SimpleUploadedFile("photo.jpg", b"broken"))


def test_validator_restores_position():
    image = image_file()
    image.seek(4)
    validate_ad_image(image)
    assert image.tell() == 4


def test_other_user_cannot_change_photo(other_client, ad):
    assert other_client.patch(f"/api/ads/{ad.pk}/", {"image": image_file()}, format="multipart").status_code == 403
    ad.refresh_from_db()
    assert not ad.image


def test_clear_image(user_client, ad):
    assert user_client.patch(f"/api/ads/{ad.pk}/", {"image": image_file()}, format="multipart").status_code == 200
    ad.refresh_from_db()
    assert ad.image
    response = user_client.patch(f"/api/ads/{ad.pk}/", {"image": None}, format="json")
    assert response.status_code == 200
    assert response.data["image"] is None
