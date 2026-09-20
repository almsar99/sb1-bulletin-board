"""Поиск и явное удаление старых неиспользуемых изображений."""

import json

from django.core.management.base import BaseCommand, CommandError

from users.media_cleanup import MINIMUM_AGE_HOURS, MediaCleanupError, cleanup_media


class Command(BaseCommand):
    help = "Найти старые неиспользуемые изображения users/ и ads/; по умолчанию ничего не удалять"

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Удалить найденные изображения")
        parser.add_argument(
            "--uploads-stopped",
            action="store_true",
            help="Подтверждаю остановку всех процессов, загружающих media или меняющих image в БД",
        )
        parser.add_argument(
            "--older-than-hours",
            type=int,
            default=MINIMUM_AGE_HOURS,
            help="Минимальное время с последнего изменения файла/метаданных в часах (не меньше 24)",
        )

    def handle(self, *args, **options):
        try:
            result = cleanup_media(
                delete=options["delete"],
                uploads_stopped=options["uploads_stopped"],
                older_than_hours=options["older_than_hours"],
            )
        except (MediaCleanupError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        prefix = "Удалено" if options["delete"] else "Кандидат"
        for name in result.deleted if options["delete"] else result.candidates:
            self.stdout.write(f"{prefix}: {json.dumps(name, ensure_ascii=False)}")
        self.stdout.write(
            f"Найдено: {len(result.candidates)}; удалено: {len(result.deleted)}; пропущено файлов: {result.skipped}."
        )
        if not options["delete"]:
            self.stdout.write("Dry-run: файлы не изменены. Для удаления нужны --delete --uploads-stopped.")
