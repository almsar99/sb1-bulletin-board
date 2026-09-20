"""Удаление аккаунта другим соединением во время HTTP-сохранения профиля."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections

from tests.conftest import PASSWORD
from users.models import User
from users.serializers import UserSerializer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "method,deletion_window,payload_kind",
    [
        ("patch", "before_update", "name"),
        ("put", "before_update", "empty"),
        ("patch", "before_update", "read_only"),
        ("put", "before_refresh", "name"),
    ],
)
def test_deleted_profile_returns_401(api_client, user, monkeypatch, mailoutbox, method, deletion_window, payload_kind):
    assert connection.vendor == "postgresql"
    login = api_client.post("/api/users/token/", {"email": user.email, "password": PASSWORD}, format="json")
    assert login.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    user_id = user.pk
    payload = {
        "name": {"first_name": "Обновлённое имя"},
        "empty": {},
        "read_only": {"id": user_id + 100, "email": "other@example.com", "role": "admin"},
    }[payload_kind]
    events = []

    def delete_committed():
        worker = connections["default"]
        try:
            worker.ensure_connection()
            assert worker.get_autocommit()
            worker_pid = worker.connection.info.backend_pid
            if deletion_window == "before_refresh":
                # Второе соединение уже видит сохранённое новое значение.
                assert User.objects.get(pk=user_id).first_name == payload["first_name"]
            _, deleted_by_model = User.objects.filter(pk=user_id).delete()
            assert deleted_by_model[User._meta.label] == 1
            return worker_pid
        finally:
            connections.close_all()

    def delete_from_other_connection():
        request_pid = connection.connection.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_pid = executor.submit(delete_committed).result(timeout=10)
        assert worker_pid != request_pid
        assert not User.objects.filter(pk=user_id).exists()
        events.append(deletion_window)

    if deletion_window == "before_update":
        original_update = UserSerializer.update

        def update_after_deletion(serializer, instance, validated_data):
            # JWT-аутентификация уже загрузила аккаунт, но запись ещё не началась.
            delete_from_other_connection()
            return original_update(serializer, instance, validated_data)

        monkeypatch.setattr(UserSerializer, "update", update_after_deletion)
    else:
        original_refresh = User.refresh_from_db

        def refresh_after_deletion(instance, *args, **kwargs):
            delete_from_other_connection()
            return original_refresh(instance, *args, **kwargs)

        monkeypatch.setattr(User, "refresh_from_db", refresh_after_deletion)

    api_client.raise_request_exception = False
    response = getattr(api_client, method)("/api/users/me/", payload, format="json")

    assert events == [deletion_window]
    assert response.status_code == 401
    assert response.data == {"detail": "Учётная запись недоступна."}
    assert response.exc_info is None
    assert not User.objects.filter(pk=user_id).exists()
    assert api_client.get("/api/users/me/").status_code == 401
    assert (
        api_client.post("/api/users/token/refresh/", {"refresh": login.data["refresh"]}, format="json").status_code
        == 401
    )
    assert not mailoutbox
