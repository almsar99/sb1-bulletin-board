"""Повторный запуск и очистка демоданных не захватывают чужие записи."""

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.admin.models import LogEntry
from django.core.management import call_command
from django.core.management.base import CommandError

from ads.models import Ad, Review
from users.models import User

pytestmark = pytest.mark.django_db
OPTIONS = {
    "admin_password": "Admin-Custom-9b!",
    "user1_password": "Anna-Custom-9b!",
    "user2_password": "Max-Custom-9b!",
}


@pytest.fixture(autouse=True)
def demo_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def seed(**kwargs):
    call_command("seed_demo", **OPTIONS, **kwargs, stdout=StringIO())


def test_seed_creates_all_kinds(demo_media):
    seed()
    assert User.objects.count() == 3
    assert Ad.objects.count() == 20
    assert set(Ad.objects.values_list("category", flat=True)) == {
        "scripts",
        "freelance",
        "courses",
        "solutions",
        "hardware",
    }
    assert Review.objects.filter(kind="review").count() == 20
    assert Review.objects.filter(kind="question", parent=None).count() == 10
    assert Review.objects.exclude(parent=None).count() == 5
    assert not Ad.objects.exclude(image="").exists()
    assert not any(p.is_file() for p in demo_media.rglob("*"))
    assert Ad.objects.filter(status="draft").count() == 3
    assert Ad.objects.filter(status="published").count() == 15
    assert Ad.objects.filter(status="archived").count() == 2
    for entry in Review.objects.filter(parent=None).select_related("ad"):
        assert entry.author_id != entry.ad.author_id
    for entry in Review.objects.exclude(parent=None).select_related("parent"):
        assert entry.parent.kind == "question"
        assert entry.ad_id == entry.parent.ad_id


def test_seed_idempotent():
    seed()
    ids = list(Ad.objects.values_list("pk", flat=True))
    counts = (User.objects.count(), Review.objects.count(), LogEntry.objects.count())
    seed()
    assert list(Ad.objects.values_list("pk", flat=True)) == ids
    assert counts == (User.objects.count(), Review.objects.count(), LogEntry.objects.count())


def test_flush_only_seeded(user, ad, review, demo_media):
    seed()
    seed(flush=True)
    assert list(User.objects.all()) == [user]
    assert list(Ad.objects.all()) == [ad]
    assert list(Review.objects.all()) == [review]
    assert not LogEntry.objects.exists()
    assert not any(p.is_file() for p in demo_media.rglob("*"))


def test_custom_passwords():
    seed()
    for name, email in [("admin", "admin"), ("user1", "anna"), ("user2", "max")]:
        assert User.objects.get(email=f"demo-{email}@market-kod.ru").check_password(OPTIONS[name + "_password"])


def test_flush_preserves_external_reply(user):
    seed()
    question = Review.objects.filter(kind="question", parent=None).first()
    answer = Review.objects.create(ad=question.ad, author=user, parent=question, text="Настоящий ответ")
    seed(flush=True)
    assert Review.objects.filter(pk=answer.pk).exists()
    assert Ad.objects.filter(pk=question.ad_id).exists()
    answer.delete()
    seed(flush=True)
    assert not Ad.objects.exists()
    assert list(User.objects.all()) == [user]


def test_existing_email_not_taken_over():
    user = User.objects.create_user(email="demo-admin@market-kod.ru", password="Existing!")
    with pytest.raises(CommandError, match="уже занят"):
        seed()
    user.refresh_from_db()
    assert user.check_password("Existing!") and not user.is_staff
    assert not Ad.objects.exists() and not LogEntry.objects.exists()


def test_defaults_only_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="Вне DEBUG"):
        call_command("seed_demo", stdout=StringIO())
    settings.DEBUG = True
    call_command("seed_demo", stdout=StringIO())
    assert User.objects.get(email="demo-admin@market-kod.ru").check_password("Demo-admin-2026!")


def test_seed_repairs_deleted_demo():
    seed()
    Ad.objects.first().delete()
    seed()
    assert Ad.objects.count() == 20
    seed(flush=True)
    assert not User.objects.exists()


def test_flush_stale_entries():
    seed()
    Review.objects.filter(parent__isnull=False).delete()
    seed(flush=True)
    assert not User.objects.exists()


def test_failed_seed_rolls_back(demo_media):
    from ads.management.commands.seed_demo import Command

    with patch.object(Command, "message", side_effect=RuntimeError("rollback")):
        with pytest.raises(RuntimeError, match="rollback"):
            seed()
    assert not Ad.objects.exists()
    assert not any(p.is_file() for p in demo_media.rglob("*"))


def test_seed_preserves_changed_existing_image(demo_media):
    seed()
    ad = Ad.objects.first()
    own_image = Path(demo_media) / "own.jpg"
    own_image.write_bytes(b"user image")
    ad.image = "own.jpg"
    ad.save()
    seed()
    ad.refresh_from_db()
    assert ad.image.name == "own.jpg" and own_image.read_bytes() == b"user image"
