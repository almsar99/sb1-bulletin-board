"""Редактирование служебных ссылок только в копиях записей журнала."""

import copy
import logging
import posixpath
import re
import traceback
from urllib.parse import unquote, urlsplit

REDACTED = "[redacted]"
_URL = re.compile(r"(?:https?://|/|%(?:25)*2f)\S*", re.IGNORECASE)
_QUERY = re.compile(r"[?#]\S*")
_SENSITIVE_PATH = re.compile(r"/(signup/confirm|password-reset)(?=/|;|$)", re.IGNORECASE)
_REFERER = re.compile(r"\b(?:referer|referrer)\s*[:=]\s*[^\r\n]*", re.IGNORECASE)


def safe_url(value):
    """Сохранить путь, скрыть параметры и хвосты ссылок, включая неверные токены."""
    value = str(value)
    for _ in range(5):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    else:
        # Не оставляем исходный текст после чрезмерного вложенного кодирования.
        return REDACTED
    value = re.sub(r"[\x00-\x20\x7f]", "", value).replace("\\", "/")
    # RAW_URI с // остаётся путём HTTP-запроса, а не network-path URL с netloc.
    if value.startswith("/"):
        value = re.sub(r"^/+", "/", value)
    try:
        parts = urlsplit(value)
    except ValueError:
        return REDACTED
    path = re.sub(r"/{2,}", "/", parts.path)
    match = _SENSITIVE_PATH.search(path)
    if not match:
        normalized = posixpath.normpath(path)
        if path.endswith("/"):
            normalized = normalized.rstrip("/") + "/"
        match = _SENSITIVE_PATH.search(normalized)
        if match:
            path = normalized
    if match:
        tail = path[match.end() :]
        public_page = match.group(1).lower() == "password-reset" and tail in {"", "/", "/done/", "/complete/"}
        if not public_page:
            path = path[: match.end()] + "/" + REDACTED
    if ";" in path:
        path = path.split(";", 1)[0] + ";" + REDACTED
    return (path or "/") + ("?" + REDACTED if "?" in value else "") + ("#" + REDACTED if "#" in value else "")


def safe_text(value):
    value = _REFERER.sub("Referer: " + REDACTED, str(value))
    value = _URL.sub(lambda match: safe_url(match.group()), value)
    return _QUERY.sub(lambda match: match.group()[0] + REDACTED, value)


def _safe_argument(value):
    if isinstance(value, str):
        return safe_url(value) if _URL.match(value) else safe_text(value)
    return value


class SensitiveRequestFilter(logging.Filter):
    def filter(self, record):
        # Не меняем request/environ и запись, которую могут читать другие обработчики.
        sanitized = copy.copy(record)
        if isinstance(record.args, dict):
            sanitized.args = copy.copy(record.args)
            sanitized.args.update({key: _safe_argument(value) for key, value in record.args.items()})
        else:
            sanitized.args = tuple(_safe_argument(value) for value in record.args)
        sanitized.msg = safe_text(sanitized.getMessage())
        sanitized.args = ()
        if record.exc_info:
            sanitized.exc_text = safe_text("".join(traceback.format_exception(*record.exc_info)))
            sanitized.exc_info = None
        elif record.exc_text:
            sanitized.exc_text = safe_text(record.exc_text)
        if record.stack_info:
            sanitized.stack_info = safe_text(record.stack_info)
        return sanitized
