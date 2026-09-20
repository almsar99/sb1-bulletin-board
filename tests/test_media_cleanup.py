"""Очистка работает только с временными файлами, без доступа к media проекта."""

import os
from io import StringIO
from time import time

import pytest
from django.core.files.storage import FileSystemStorage, InMemoryStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from ads.models import Ad, AdStatus
from users.media_cleanup import cleanup_media
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def media_area(tmp_path, settings, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    settings.MEDIA_ROOT = root
    clock = time() + 3 * 24 * 3600
    monkeypatch.setattr("users.media_cleanup.time", lambda: clock)
    return root, clock


def write_image(root, name, *, format="PNG", modified=None):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3, 3), "red").save(path, format=format)
    if modified is not None:
        os.utime(path, (modified, modified))
    return path


def run_command(*args):
    output = StringIO()
    call_command("cleanup_media", *args, stdout=output)
    return output.getvalue()


def test_default_command_only_reports_orphans(media_area):
    root, _ = media_area
    image = write_image(root, "users/old.png")
    output = run_command()
    assert 'Кандидат: "users/old.png"' in output
    assert "удалено: 0" in output
    assert "Dry-run" in output
    assert image.exists()


def test_delete_requires_stopped_writers_acknowledgement(media_area):
    root, _ = media_area
    image = write_image(root, "users/old.png")
    with pytest.raises(CommandError, match="--uploads-stopped"):
        run_command("--delete")
    assert image.exists()


def test_acknowledgement_alone_does_not_delete(media_area):
    root, _ = media_area
    image = write_image(root, "users/old.png")
    assert "Dry-run" in run_command("--uploads-stopped")
    assert image.exists()


@pytest.mark.parametrize("hours", ["-1", "0", "23"])
def test_cannot_reduce_minimum_grace_period(media_area, hours):
    root, _ = media_area
    image = write_image(root, "users/old.png")
    with pytest.raises(CommandError, match="24 часа"):
        run_command("--delete", "--uploads-stopped", "--older-than-hours", hours)
    assert image.exists()


def test_unrepresentable_grace_has_a_clear_error(media_area):
    with pytest.raises(CommandError, match="Слишком большое"):
        run_command("--older-than-hours", str(10**400))


@pytest.mark.parametrize(
    "name,format", [("users/old.JPG", "JPEG"), ("ads/2026/09/old.png", "PNG"), ("users/old.webp", "WEBP")]
)
def test_explicit_delete_removes_old_supported_orphan(media_area, name, format):
    root, _ = media_area
    image = write_image(root, name, format=format)
    output = run_command("--delete", "--uploads-stopped")
    assert "Найдено: 1; удалено: 1" in output
    assert not image.exists()
    assert image.parent.is_dir()  # Пустые каталоги не удаляются.


def test_all_model_references_preserved_regardless_of_status_or_scope(media_area, user):
    root, _ = media_area
    profile_image = write_image(root, "ads/2026/09/profile.png")
    ad_image = write_image(root, "users/archived.png")
    draft_image = write_image(root, "ads/2026/09/draft.png")
    orphan = write_image(root, "users/orphan.png")
    User.objects.filter(pk=user.pk).update(image="ads/2026/09/profile.png", is_active=False)
    for name, status in [("users/archived.png", AdStatus.ARCHIVED), ("ads/2026/09/draft.png", AdStatus.DRAFT)]:
        Ad.objects.create(author=user, title="Объявление", description="Текст", price=1, image=name, status=status)
    run_command("--delete", "--uploads-stopped")
    assert profile_image.exists()
    assert ad_image.exists()
    assert draft_image.exists()
    assert not orphan.exists()


def test_normalized_and_shared_references_are_preserved(media_area, user, other_user):
    root, _ = media_area
    image = write_image(root, "ads/2026/09/photo.png")
    User.objects.filter(pk=user.pk).update(image="users/../ads/2026/09/PHOTO.png")
    User.objects.filter(pk=other_user.pk).update(image="ads/2026/09/photo.png")
    user.delete()
    assert cleanup_media(delete=True, uploads_stopped=True).candidates == []
    assert image.exists()
    User.objects.filter(pk=other_user.pk).update(image="users/../ads/2026/09/PHOTO.png")
    assert cleanup_media(delete=True, uploads_stopped=True).candidates == []
    assert image.exists()


def test_fresh_files_and_extended_grace_preserved(media_area):
    root, clock = media_area
    fresh = write_image(root, "users/fresh.png", modified=clock - 3600)
    recent = write_image(root, "users/recent.png", modified=clock - 36 * 3600)
    old = write_image(root, "users/old.png", modified=clock - 49 * 3600)
    run_command("--delete", "--uploads-stopped", "--older-than-hours", "48")
    assert fresh.exists()
    assert recent.exists()
    assert not old.exists()


def test_preserved_old_mtime_does_not_override_recent_metadata(media_area, monkeypatch):
    root, _ = media_area
    actual_now = time()
    image = write_image(root, "users/copied.png", modified=actual_now - 7 * 24 * 3600)
    monkeypatch.setattr("users.media_cleanup.time", lambda: actual_now + 3600)
    assert cleanup_media(delete=True, uploads_stopped=True).candidates == []
    assert image.exists()


def test_exact_age_boundary_can_be_deleted(media_area):
    root, clock = media_area
    image = write_image(root, "users/old.png", modified=clock - 24 * 3600)
    run_command("--delete", "--uploads-stopped")
    assert not image.exists()


def test_hidden_service_other_scope_and_nonimages_are_untouched(media_area):
    root, _ = media_area
    protected = [
        write_image(root, "other/old.png"),
        write_image(root, "old.png"),
        write_image(root, "users/.old.png"),
        write_image(root, "ads/.backup/old.png"),
        write_image(root, "users/old.gif", format="GIF"),
        write_image(root, "users/wrong.jpg", format="PNG"),
    ]
    for name in ["users/report.pdf", "ads/readme.txt", "users/document.png", "users/.keep"]:
        path = root / name
        path.write_bytes(b"document, not an image")
        protected.append(path)
    run_command("--delete", "--uploads-stopped")
    assert all(path.exists() for path in protected)


def test_oversized_image_is_conservatively_preserved(media_area):
    root, _ = media_area
    image = write_image(root, "users/oversized.png")
    with image.open("ab") as stream:
        stream.write(b"\0" * (5 * 1024 * 1024))
    assert cleanup_media(delete=True, uploads_stopped=True).candidates == []
    assert image.exists()


def test_symlinks_and_hardlinks_cannot_target_other_files(media_area, tmp_path):
    root, _ = media_area
    outside = tmp_path / "outside"
    target = write_image(outside, "target.png")
    (root / "users").mkdir()
    file_link = root / "users/link.png"
    file_link.symlink_to(target)
    directory_link = root / "users/outside"
    directory_link.symlink_to(outside, target_is_directory=True)
    os.link(target, root / "users/hardlink.png")
    (root / "ads").symlink_to(outside, target_is_directory=True)
    assert cleanup_media(delete=True, uploads_stopped=True).candidates == []
    assert file_link.is_symlink()
    assert directory_link.is_symlink()
    assert target.exists()
    assert (root / "users/hardlink.png").exists()


def test_symlink_media_root_is_rejected(media_area, tmp_path, settings):
    root, _ = media_area
    target = write_image(root, "users/old.png")
    linked_root = tmp_path / "linked-media"
    linked_root.symlink_to(root, target_is_directory=True)
    settings.MEDIA_ROOT = linked_root
    with pytest.raises(CommandError, match="символической ссылкой"):
        run_command("--delete", "--uploads-stopped")
    assert target.exists()


@pytest.mark.parametrize("directory_link", [False, True])
def test_referenced_symlink_aborts_before_any_deletion(media_area, user, directory_link):
    root, _ = media_area
    original = write_image(root, "users/original.png")
    orphan = write_image(root, "users/orphan.png")
    if directory_link:
        (root / "ads").mkdir()
        (root / "ads/alias").symlink_to(root / "users", target_is_directory=True)
        name = "ads/alias/original.png"
    else:
        (root / "users/alias.png").symlink_to(original)
        name = "users/alias.png"
    User.objects.filter(pk=user.pk).update(image=name)
    with pytest.raises(CommandError, match="символическую ссылку"):
        run_command("--delete", "--uploads-stopped")
    assert original.exists()
    assert orphan.exists()


def test_outside_reference_aborts_before_any_deletion(media_area, user):
    root, _ = media_area
    orphan = write_image(root, "users/orphan.png")
    User.objects.filter(pk=user.pk).update(image="../outside/photo.png")
    with pytest.raises(CommandError, match="вне MEDIA_ROOT"):
        run_command("--delete", "--uploads-stopped")
    assert orphan.exists()


def test_literal_backslash_in_referenced_symlink_is_not_treated_as_a_directory(media_area, user):
    root, _ = media_area
    original = write_image(root, "users/original.png")
    name = "users/alias\\name.png"
    (root / name).symlink_to(original)
    User.objects.filter(pk=user.pk).update(image=name)
    with pytest.raises(CommandError, match="символическую ссылку"):
        run_command("--delete", "--uploads-stopped")
    assert original.exists()


@pytest.mark.parametrize("model", [User, Ad])
def test_nonlocal_storage_is_rejected_before_traversal(media_area, monkeypatch, model):
    root, _ = media_area
    image = write_image(root, "users/old.png")
    monkeypatch.setattr(model._meta.get_field("image"), "storage", InMemoryStorage())
    with pytest.raises(CommandError, match="FileSystemStorage"):
        run_command("--delete", "--uploads-stopped")
    assert image.exists()


def test_different_storage_root_is_rejected(media_area, tmp_path, monkeypatch):
    root, _ = media_area
    image = write_image(root, "users/old.png")
    monkeypatch.setattr(User._meta.get_field("image"), "storage", FileSystemStorage(location=tmp_path / "other"))
    with pytest.raises(CommandError, match="MEDIA_ROOT"):
        run_command("--delete", "--uploads-stopped")
    assert image.exists()


def test_missing_root_is_a_noop(media_area):
    root, _ = media_area
    root.rmdir()
    assert "Найдено: 0; удалено: 0" in run_command("--delete", "--uploads-stopped")
    assert not root.exists()


def test_empty_media_root_is_rejected(media_area, settings):
    settings.MEDIA_ROOT = ""
    with pytest.raises(CommandError, match="MEDIA_ROOT"):
        run_command()


def test_image_changed_during_scan_is_not_deleted(media_area, monkeypatch):
    root, clock = media_area
    image = write_image(root, "users/old.png")

    def concurrent_change(*args):
        os.utime(image, (clock, clock))
        return True

    monkeypatch.setattr("users.media_cleanup._verified_image", concurrent_change)
    assert cleanup_media(delete=True, uploads_stopped=True).candidates == []
    assert image.exists()


def test_dry_run_output_escapes_control_characters(media_area):
    root, _ = media_area
    image = write_image(root, "users/unusual\nname.png")
    output = run_command()
    assert '"users/unusual\\nname.png"' in output
    assert image.exists()
