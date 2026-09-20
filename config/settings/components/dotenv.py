"""Загрузка файла .env в переменные окружения.

Файл используется только при локальном запуске: в контейнерах и на сервере переменные
передаются снаружи. Уже заданные значения не перезаписываются, поэтому внешнее окружение
всегда имеет приоритет над содержимым файла.
"""

import os
from pathlib import Path


def load_dotenv(path, *, environ=None):
    """Прочитать файл и добавить недостающие переменные. Вернуть число добавленных."""
    target = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return 0

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw_value = line.partition("=")
        name = name.strip()
        if not name or name in target:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        target[name] = value
        loaded += 1
    return loaded
