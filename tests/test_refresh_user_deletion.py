"""Удаление пользователя другим соединением между двумя проверками refresh."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from tests.conftest import PASSWORD
from users.models import User

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("token_kind", ["current", "legacy"])
def test_refresh_account_deleted_after_authentication_returns_401(
    api_client, user, monkeypatch, mailoutbox, token_kind
):
    assert connection.vendor == "postgresql"
    login = api_client.post("/api/users/token/", {"email": user.email, "password": PASSWORD}, format="json")
    assert login.status_code == 200
    refresh = login.data["refresh"] if token_kind == "current" else str(RefreshToken.for_user(user))
    user_id = user.pk
    original_validate = TokenRefreshSerializer.validate
    events = []

    def delete_committed():
        worker = connections["default"]
        try:
            worker.ensure_connection()
            assert worker.get_autocommit()
            worker_pid = worker.connection.info.backend_pid
            _, deleted_by_model = User.objects.filter(pk=user_id).delete()
            assert deleted_by_model[User._meta.label] == 1
            return worker_pid
        finally:
            connections.close_all()

    def delete_before_parent_validate(serializer, attrs):
        # Собственная JWT-проверка уже загрузила пользователя. SimpleJWT ниже
        # повторно читает БД; второе соединение фиксирует удаление до этого чтения.
        request_pid = connection.connection.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker_pid = executor.submit(delete_committed).result(timeout=10)
        assert worker_pid != request_pid
        assert not User.objects.filter(pk=user_id).exists()
        events.append("committed deletion")
        return original_validate(serializer, attrs)

    monkeypatch.setattr(TokenRefreshSerializer, "validate", delete_before_parent_validate)
    api_client.raise_request_exception = False
    response = api_client.post("/api/users/token/refresh/", {"refresh": refresh}, format="json")

    assert events == ["committed deletion"]
    assert response.status_code == 401
    assert response.data == {"detail": "Недействительный токен.", "code": "token_not_valid"}
    assert response.exc_info is None
    assert "access" not in response.data
    assert not User.objects.filter(pk=user_id).exists()
    assert not mailoutbox
