"""Удаление отправителя другим соединением после проверки сессии и до блокировки."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections
from django.test import Client

from ads.models import Ad, AdStatus
from messaging.models import Message, Thread
from tests.conftest import PASSWORD
from users.models import User

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("flow", ["start", "reply"])
def test_sender_deleted_before_lock_returns_404(ad, other_user, monkeypatch, mailoutbox, flow):
    assert connection.vendor == "postgresql"
    ad.status = AdStatus.PUBLISHED
    ad.save(update_fields=["status"])
    sender_id = other_user.pk
    if flow == "start":
        url = f"/ad/{ad.pk}/message/"
    else:
        thread = Thread.objects.create(ad=ad, initiator=other_user)
        url = f"/messages/{thread.pk}/"

    client = Client(enforce_csrf_checks=True, raise_request_exception=False)
    assert client.get("/login/").status_code == 200
    login = client.post(
        "/login/",
        {"username": other_user.email, "password": PASSWORD},
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert login.status_code == 302
    assert client.session["_auth_user_id"] == str(sender_id)

    select_for_update = User.objects.select_for_update
    events = []

    def delete_sender_committed():
        worker = connections["default"]
        try:
            worker.ensure_connection()
            assert worker.get_autocommit()
            worker_pid = worker.connection.info.backend_pid
            _, deleted_by_model = User.objects.filter(pk=sender_id).delete()
            assert deleted_by_model[User._meta.label] == 1
            return worker_pid
        finally:
            connections.close_all()

    def delete_before_user_lock(*args, **kwargs):
        # Сессия и форма уже проверены. Отдельное соединение коммитит удаление,
        # которое не отменяется при откате транзакции отправки сообщения.
        assert connection.in_atomic_block
        request_pid = connection.connection.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_pid = executor.submit(delete_sender_committed).result(timeout=10)
        assert worker_pid != request_pid
        assert not User.objects.filter(pk=sender_id).exists()
        events.append("committed sender deletion")
        return select_for_update(*args, **kwargs)

    monkeypatch.setattr(User.objects, "select_for_update", delete_before_user_lock)
    response = client.post(
        url,
        {"text": "Сообщение после проверки сессии"},
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )

    assert events == ["committed sender deletion"]
    assert response.status_code == 404
    assert response.exc_info is None
    assert not User.objects.filter(pk=sender_id).exists()
    assert Ad.objects.filter(pk=ad.pk).exists()
    assert not Thread.objects.exists()
    assert not Message.objects.exists()
    assert not mailoutbox
