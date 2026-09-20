"""Совместимость исторической миграции пользователей с актуальной схемой заявок."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATE_FROM = ("users", "0002_user_password_changed_at")
MIGRATE_TO = ("users", "0003_user_email_verified_at")


def test_existing_user_becomes_verified_after_migration():
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([MIGRATE_FROM])

        old_apps = executor.loader.project_state([MIGRATE_FROM]).apps
        OldUser = old_apps.get_model("users", "User")

        user = OldUser.objects.create(
            email="existing-before-migration@example.com",
            password="!",
            is_active=True,
            is_staff=False,
        )
        user_id = user.pk

        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATE_TO])

        new_apps = executor.loader.project_state([MIGRATE_TO]).apps
        NewUser = new_apps.get_model("users", "User")

        migrated = NewUser.objects.get(pk=user_id)

        assert migrated.email == "existing-before-migration@example.com"
        assert migrated.email_verified_at is not None
        assert migrated.is_active is True
    finally:
        MigrationExecutor(connection).migrate(latest_targets)
