"""Удаление объявления другим соединением между проверкой прав и блокировкой."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.db import connection, connections

from ads.models import Ad, AdStatus
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db(transaction=True)

EDIT_DATA = {
    "title": "Изменённое объявление",
    "price": 100,
    "description": "Обновлённое описание",
    "category": "scripts",
}


@pytest.mark.parametrize("actor_fixture", ["user", "admin"])
@pytest.mark.parametrize("flow", ["api_patch", "api_put", "web_edit", "web_status"])
def test_deleted_ad_between_lookup_and_lock_returns_404(
    request, api_client, client, ad, mailoutbox, actor_fixture, flow
):
    assert connection.vendor == "postgresql"
    actor = request.getfixturevalue(actor_fixture)
    publish = actor.is_admin and flow != "web_edit"
    ad.status = AdStatus.DRAFT if publish else AdStatus.PUBLISHED
    ad.save(update_fields=["status"])

    if flow.startswith("api_"):
        login = api_client.post("/api/users/token/", {"email": actor.email, "password": PASSWORD}, format="json")
        assert login.status_code == 200
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        api_client.raise_request_exception = False
    else:
        login = client.post("/login/", {"username": actor.email, "password": PASSWORD})
        assert login.status_code == 302
        assert client.session["_auth_user_id"] == str(actor.pk)
        client.raise_request_exception = False

    ad_id = ad.pk
    select_for_update = Ad.objects.select_for_update

    def delete_ad():
        worker = connections["default"]
        try:
            worker.ensure_connection()
            assert worker.get_autocommit()
            worker_pid = worker.connection.info.backend_pid
            _, deleted_by_model = Ad.objects.filter(pk=ad_id).delete()
            assert deleted_by_model[Ad._meta.label] == 1
            return worker_pid
        finally:
            connections.close_all()

    def delete_before_lock(*args, **kwargs):
        # Первичный lookup, проверка прав и валидация уже завершились. Удаление
        # коммитится отдельно и не отменяется откатом транзакции update_ad().
        request_pid = connection.connection.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_pid = executor.submit(delete_ad).result(timeout=10)
        assert worker_pid != request_pid
        assert not Ad.objects.filter(pk=ad_id).exists()
        return select_for_update(*args, **kwargs)

    with patch.object(Ad.objects, "select_for_update", side_effect=delete_before_lock) as locked_lookup:
        if flow.startswith("api_"):
            payload = {**EDIT_DATA, **({"status": AdStatus.PUBLISHED} if publish else {})}
            response = getattr(api_client, flow.removeprefix("api_"))(f"/api/ads/{ad_id}/", payload, format="json")
        elif flow == "web_edit":
            response = client.post(f"/ad/{ad_id}/edit/", EDIT_DATA)
        else:
            target = AdStatus.PUBLISHED if publish else AdStatus.ARCHIVED
            response = client.post(f"/ad/{ad_id}/status/", {"status": target})

    assert locked_lookup.call_count == 1
    assert response.status_code == 404
    assert not Ad.objects.exists()
    assert not mailoutbox
