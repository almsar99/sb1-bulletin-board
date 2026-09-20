"""Изображение профиля: одинаковые ограничения API, модели и административной формы."""

from io import BytesIO

import pytest
from django.contrib import admin as django_admin
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from PIL import Image

from config.validators import MAX_IMAGE_SIZE
from tests.conftest import PASSWORD
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def image_storage(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def profile_client(api_client, user):
    response = api_client.post("/api/users/token/", {"email": user.email, "password": PASSWORD}, format="json")
    assert response.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return api_client


def image_file(name="avatar.png", image_format="PNG", *, size=None):
    buffer = BytesIO()
    Image.new("RGB", (24, 16), "#9bad83").save(buffer, format=image_format)
    content = buffer.getvalue()
    if size is not None:
        content += b"\0" * (size - len(content))
    return SimpleUploadedFile(name, content, content_type=f"image/{image_format.lower()}")


@pytest.mark.parametrize("name,image_format", [("avatar.jpg", "JPEG"), ("avatar.PNG", "PNG"), ("avatar.webp", "WEBP")])
def test_profile_image_accepts_supported_formats(profile_client, user, name, image_format):
    response = profile_client.patch("/api/users/me/", {"image": image_file(name, image_format)}, format="multipart")
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.image.name.startswith("users/")
    assert user.image.storage.exists(user.image.name)
    assert response.data["image"].endswith(user.image.name)


def test_profile_image_accepts_exact_size_limit(profile_client, user):
    response = profile_client.patch("/api/users/me/", {"image": image_file(size=MAX_IMAGE_SIZE)}, format="multipart")
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.image.size == MAX_IMAGE_SIZE


def test_profile_image_rejects_exceeded_size_limit(profile_client, user):
    response = profile_client.patch(
        "/api/users/me/", {"image": image_file(size=MAX_IMAGE_SIZE + 1)}, format="multipart"
    )
    assert response.status_code == 400
    assert "5 МБ" in str(response.data["image"])
    user.refresh_from_db()
    assert not user.image


def test_profile_image_rejects_unsupported_format(profile_client, user):
    response = profile_client.patch("/api/users/me/", {"image": image_file("avatar.gif", "GIF")}, format="multipart")
    assert response.status_code == 400
    assert "JPEG, PNG и WebP" in str(response.data["image"])
    user.refresh_from_db()
    assert not user.image


def test_profile_image_rejects_mismatch(profile_client):
    response = profile_client.patch("/api/users/me/", {"image": image_file("avatar.jpg", "PNG")}, format="multipart")
    assert response.status_code == 400
    assert "расширению" in str(response.data["image"])


def test_profile_image_rejects_corrupt_file(profile_client):
    response = profile_client.patch(
        "/api/users/me/", {"image": SimpleUploadedFile("avatar.png", b"broken image")}, format="multipart"
    )
    assert response.status_code == 400
    assert "image" in response.data


def test_profile_image_can_be_cleared(profile_client, user):
    assert profile_client.patch("/api/users/me/", {"image": image_file()}, format="multipart").status_code == 200
    response = profile_client.patch("/api/users/me/", {"image": None}, format="json")
    assert response.status_code == 200
    assert response.data["image"] is None
    user.refresh_from_db()
    assert not user.image


def test_model_rejects_invalid_profile_image(user):
    user.image = image_file("avatar.gif", "GIF")
    with pytest.raises(ValidationError) as error:
        user.full_clean()
    assert "image" in error.value.message_dict


def test_admin_form_rejects_oversized_profile_image(user, admin):
    request = RequestFactory().get(f"/admin/users/user/{user.pk}/change/")
    request.user = admin
    form_class = django_admin.site._registry[User].get_form(request, obj=user)
    form = form_class(
        data={"email": user.email, "role": user.role},
        files={"image": image_file(size=MAX_IMAGE_SIZE + 1)},
        instance=user,
    )
    assert not form.is_valid()
    assert "5 МБ" in str(form.errors["image"])
