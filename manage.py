#!/usr/bin/env python
"""Командная строка Django."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - окружение без Django
        raise ImportError(
            "Django не установлен. Активируйте виртуальное окружение и выполните "
            "pip install -r requirements/dev.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
