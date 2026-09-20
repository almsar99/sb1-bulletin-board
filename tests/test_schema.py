"""Публичная схема, разделы, алиасы и авторизация документации."""

import pytest

pytestmark = pytest.mark.django_db


def test_schema_contract(api_client):
    response = api_client.get("/api/schema/?format=json")
    assert response.status_code == 200
    schema = response.json()
    assert "/ads/" not in schema["paths"]
    assert "/users/reset_password/" not in schema["paths"]
    assert "/users/reset_password_confirm/" not in schema["paths"]
    assert "/users/reset_password_confirm" not in schema["paths"]
    for path, operations in schema["paths"].items():
        assert path.startswith("/api/")
        for method, operation in operations.items():
            if method in ["get", "post", "put", "patch", "delete"]:
                assert operation["summary"]
                assert set(operation["tags"]) <= {"Учётные записи", "Объявления", "Отзывы"}
    assert schema["components"]["securitySchemes"]["jwtAuth"]["scheme"] == "bearer"
    assert "204" in schema["paths"]["/api/users/reset_password/"]["post"]["responses"]
    assert schema["paths"]["/api/ads/{id}/"]["get"]["security"] == [{"jwtAuth": []}]


def test_documentation_opens(api_client):
    response = api_client.get("/api/docs/")
    assert response.status_code == 200
    assert b"SwaggerUIBundle" in response.content
