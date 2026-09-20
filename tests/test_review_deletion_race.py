"""Удаление отзыва другим соединением до сохранения и перед повторным чтением."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.db import connection, connections
from rest_framework.test import APIClient

from ads.models import Ad, AdStatus, Review
from ads.serializers import ReviewSerializer
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db(transaction=True)
UPDATED_TEXT = "Исправленный отзыв"


@pytest.mark.parametrize("actor_fixture", ["user", "admin"])
@pytest.mark.parametrize("payload_kind", ["text", "empty", "read_only"])
def test_deleted_review_between_lookup_and_save_is_not_recreated(
    request, api_client, ad, review, admin, mailoutbox, actor_fixture, payload_kind
):
    assert connection.vendor == "postgresql"
    actor = request.getfixturevalue(actor_fixture)
    login = api_client.post("/api/users/token/", {"email": actor.email, "password": PASSWORD}, format="json")
    assert login.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    api_client.raise_request_exception = False
    moderator = APIClient()
    moderator_login = moderator.post("/api/users/token/", {"email": admin.email, "password": PASSWORD}, format="json")
    assert moderator_login.status_code == 200
    moderator_access = moderator_login.data["access"]
    review_id = review.pk
    url = f"/api/ads/{ad.pk}/reviews/{review_id}/"
    payload = {
        "text": {"text": UPDATED_TEXT},
        "empty": {},
        "read_only": {"author": admin.pk, "ad": ad.pk + 1},
    }[payload_kind]
    original_update = ReviewSerializer.update

    def delete_review():
        worker = connections["default"]
        try:
            worker.ensure_connection()
            assert worker.get_autocommit()
            worker_pid = worker.connection.info.backend_pid
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {moderator_access}")
            assert client.delete(url).status_code == 204
            assert not Review.objects.filter(pk=review_id).exists()
            return worker_pid
        finally:
            connections.close_all()

    def delete_before_save(serializer, instance, validated_data):
        # Lookup, проверка прав и валидация PATCH завершены. DELETE администратора
        # фиксируется отдельным HTTP-запросом до сохранения устаревшего instance.
        request_pid = connection.connection.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_pid = executor.submit(delete_review).result(timeout=10)
        assert worker_pid != request_pid
        assert not Review.objects.filter(pk=review_id).exists()
        return original_update(serializer, instance, validated_data)

    with patch.object(ReviewSerializer, "update", autospec=True, side_effect=delete_before_save) as save:
        response = api_client.patch(url, payload, format="json")

    assert save.call_count == 1
    assert response.status_code == 404
    assert not Review.objects.exists()
    assert Ad.objects.filter(pk=ad.pk).exists()
    assert not mailoutbox


@pytest.mark.parametrize("actor_fixture", ["user", "admin"])
@pytest.mark.parametrize("ad_status", [AdStatus.PUBLISHED, AdStatus.DRAFT])
def test_deleted_review_between_save_and_reload_returns_404(
    request, api_client, ad, review, other_user, mailoutbox, actor_fixture, ad_status
):
    assert connection.vendor == "postgresql"
    actor = request.getfixturevalue(actor_fixture)
    # Свой отзыв можно редактировать и у чужого скрытого объявления.
    ad.author = other_user
    ad.status = ad_status
    ad.save(update_fields=["author", "status"])
    login = api_client.post("/api/users/token/", {"email": actor.email, "password": PASSWORD}, format="json")
    assert login.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    api_client.raise_request_exception = False

    review_id = review.pk
    original_update = ReviewSerializer.update

    def delete_review():
        worker = connections["default"]
        try:
            worker.ensure_connection()
            assert worker.get_autocommit()
            worker_pid = worker.connection.info.backend_pid
            assert Review.objects.get(pk=review_id).text == UPDATED_TEXT
            _, deleted_by_model = Review.objects.filter(pk=review_id).delete()
            assert deleted_by_model[Review._meta.label] == 1
            return worker_pid
        finally:
            connections.close_all()

    def delete_after_save(serializer, instance, validated_data):
        saved = original_update(serializer, instance, validated_data)
        request_pid = connection.connection.info.backend_pid
        # Второе соединение видит сохранённый текст и фиксирует удаление до
        # повторной выборки, которую perform_update делает для безопасного ответа.
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_pid = executor.submit(delete_review).result(timeout=10)
        assert worker_pid != request_pid
        assert not Review.objects.filter(pk=review_id).exists()
        return saved

    with patch.object(ReviewSerializer, "update", autospec=True, side_effect=delete_after_save) as save:
        response = api_client.patch(f"/api/ads/{ad.pk}/reviews/{review_id}/", {"text": UPDATED_TEXT}, format="json")

    assert save.call_count == 1
    assert response.status_code == 404
    assert not Review.objects.exists()
    assert Ad.objects.filter(pk=ad.pk, author=other_user, status=ad_status).exists()
    assert not mailoutbox
