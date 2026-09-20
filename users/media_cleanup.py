"""Консервативная очистка локальных изображений после остановки всех записывающих процессов."""

import errno
import os
import posixpath
import stat
import unicodedata
import warnings
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from time import time

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from PIL import Image, UnidentifiedImageError

from ads.models import Ad
from config.validators import IMAGE_FORMATS, MAX_IMAGE_SIZE
from users.models import User

MINIMUM_AGE_HOURS = 24
IMAGE_DIRECTORIES = ("users", "ads")


class MediaCleanupError(ValueError):
    pass


@dataclass
class CleanupResult:
    candidates: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: int = 0


def _reference_key(name):
    # Сохраняем также варианты регистра/Unicode на нечувствительных файловых системах.
    return unicodedata.normalize("NFC", posixpath.normpath(str(name).replace("\\", "/"))).casefold()


def _media_root():
    if not settings.MEDIA_ROOT:
        raise MediaCleanupError("MEDIA_ROOT должен указывать на локальный каталог проекта.")
    root = Path(settings.MEDIA_ROOT).absolute()
    if root.is_symlink():
        raise MediaCleanupError("MEDIA_ROOT не должен быть символической ссылкой.")
    for model in (User, Ad):
        storage = model._meta.get_field("image").storage
        # __class__ раскрывает Django LazyObject, но не разрешает сторонние подклассы storage.
        if storage.__class__ is not FileSystemStorage or Path(storage.location).resolve() != root.resolve():
            raise MediaCleanupError("Очистка поддерживает только FileSystemStorage изображений внутри MEDIA_ROOT.")
    return root


def _check_reference(root_fd, name):
    """Не удалять цель используемой ссылки, даже если walker пропустит сам symlink."""
    # FileSystemStorage.path() нормализует '..', но на POSIX не заменяет буквальный '\\'.
    normalized = posixpath.normpath(str(name))
    parts = normalized.split("/")
    if normalized.startswith("/") or parts[0] == "..":
        raise MediaCleanupError("В БД найден image-путь вне MEDIA_ROOT. Проверьте ссылки перед очисткой.")
    with ExitStack() as opened:
        directory_fd = root_fd
        for index, part in enumerate(parts):
            try:
                current = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(current.st_mode):
                    raise MediaCleanupError("Используемый image-путь содержит символическую ссылку; очистка отменена.")
                if index < len(parts) - 1:
                    if not stat.S_ISDIR(current.st_mode):
                        return
                    directory_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                    opened.callback(os.close, directory_fd)
            except FileNotFoundError:
                return


def _used_images(root_fd):
    used = set()
    for model in (User, Ad):
        for name in model.objects.values_list("image", flat=True).iterator():
            if name:
                _check_reference(root_fd, name)
                used.add(_reference_key(name))
    return used


def _same_file(first, second):
    return (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) == (
        second.st_dev,
        second.st_ino,
        second.st_size,
        second.st_mtime_ns,
        second.st_ctime_ns,
    )


def _verified_image(directory_fd, name, original):
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(descriptor, "rb") as stream:
            if not _same_file(original, os.fstat(stream.fileno())):
                return False
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(stream) as image:
                    if image.format != IMAGE_FORMATS[Path(name).suffix.lower()]:
                        return False
                    image.verify()
            return _same_file(original, os.fstat(stream.fileno()))
    except (
        OSError,
        ValueError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return False


def cleanup_media(*, delete=False, uploads_stopped=False, older_than_hours=MINIMUM_AGE_HOURS):
    """Dry-run можно выполнять онлайн; удаление требует остановки всех писателей БД и media.

    Флаг подтверждает внешнюю остановку, а не создаёт блокировку. Снимок ссылок и unlink
    не атомарны: между ними нельзя загружать файлы или менять поля image из любого процесса.
    """
    if not isinstance(older_than_hours, int) or older_than_hours < MINIMUM_AGE_HOURS:
        raise MediaCleanupError(f"Минимальный возраст изображений — {MINIMUM_AGE_HOURS} часа.")
    if delete and not uploads_stopped:
        raise MediaCleanupError("Для удаления остановите все процессы записи image/media и укажите --uploads-stopped.")
    try:
        cutoff = time() - older_than_hours * 3600
    except OverflowError as exc:
        raise MediaCleanupError("Слишком большое значение --older-than-hours.") from exc
    root = _media_root()
    result = CleanupResult()
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return result
    try:
        used = _used_images(root_fd)
        for scope in IMAGE_DIRECTORIES:
            try:
                scope_fd = os.open(scope, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            except OSError as exc:
                if exc.errno not in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                    raise
                continue
            try:
                for directory, subdirectories, names, directory_fd in os.fwalk(
                    ".", dir_fd=scope_fd, follow_symlinks=False
                ):
                    subdirectories[:] = sorted(name for name in subdirectories if not name.startswith("."))
                    for name in sorted(names):
                        relative = posixpath.normpath(posixpath.join(scope, directory, name))
                        if (
                            name.startswith(".")
                            or Path(name).suffix.lower() not in IMAGE_FORMATS
                            or _reference_key(relative) in used
                        ):
                            result.skipped += 1
                            continue
                        try:
                            original = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if (
                            not stat.S_ISREG(original.st_mode)
                            or original.st_nlink != 1
                            or original.st_size > MAX_IMAGE_SIZE
                            or max(original.st_mtime, original.st_ctime) > cutoff
                            or not _verified_image(directory_fd, name, original)
                        ):
                            result.skipped += 1
                            continue
                        try:
                            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                            if not _same_file(original, current):
                                result.skipped += 1
                                continue
                            result.candidates.append(relative)
                            if delete:
                                os.unlink(name, dir_fd=directory_fd)
                                result.deleted.append(relative)
                        except FileNotFoundError:
                            continue
            finally:
                os.close(scope_fd)
    finally:
        os.close(root_fd)
    return result
