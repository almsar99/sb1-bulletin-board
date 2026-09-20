"""Служебные URL не попадают в access/error журналы; запросы не изменяются."""

import logging
import os
import subprocess
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils.log import log_response
from gunicorn.config import Config

from config.gunicorn import SafeLogger
from config.logging import REDACTED, SensitiveRequestFilter, safe_text, safe_url
from config.settings.components.logging import LOGGING, deployment_logging_from_env

TOKEN = "synthetic-secret-token"
UID = "synthetic-private-uid"
QUERY = "synthetic-query-value"
REFERER = "synthetic-referrer-value"


@pytest.mark.parametrize(
    "path",
    [
        f"/signup/confirm/{TOKEN}/",
        f"//signup/confirm/{TOKEN}/",
        f"//password-reset/{UID}/{TOKEN}/",
        quote(f"//signup/confirm/{TOKEN}/", safe=""),
        quote(f"//password-reset/{UID}/{TOKEN}/", safe=""),
        f"/signup/./confirm/{TOKEN}/",
        f"/signup/x/../confirm/{TOKEN}/",
        f"/signup/%2e/confirm/{TOKEN}/",
        f"/signup/confirm/../{TOKEN}/",
        f"/signup/confirm/{TOKEN}/extra/",
        f"/password-reset/{UID}/{TOKEN}/",
        f"/password-reset/{UID}/",
        f"/password-reset/{UID}/{TOKEN}",
        f"/PREFIX/PASSWORD-RESET/{UID}/{TOKEN}/",
        f"/signup//confirm/{TOKEN}/",
        f"/signup/confirm/<invalid-{TOKEN}>/",
        f"/signup/confirm/'invalid-{TOKEN}'/",
        f"/signup/confirm/{TOKEN}%20another-secret/",
        quote(f"/password-reset/{UID}/{TOKEN}/", safe=""),
        quote(quote(f"/password-reset/{UID}/{TOKEN}/", safe=""), safe=""),
        f"/passw%6frd-reset/{UID}/{TOKEN}/",
        f"https://example.test/password-reset/{UID}/{TOKEN}/",
        f"/password-reset;token={TOKEN}",
    ],
)
def test_sensitive_url_masks_uid_and_invalid_tokens(path):
    result = safe_url(path + f"?token={QUERY}#fragment={QUERY}")
    assert TOKEN not in result
    assert UID not in result
    assert QUERY not in result
    assert "another-secret" not in result
    assert REDACTED in result


@pytest.mark.parametrize("path", ["/ads/", "/password-reset/", "/password-reset/done/"])
def test_safe_routes_remain_useful(path):
    assert safe_url(path) == path
    assert safe_url(path + f"?token={QUERY}") == path + "?" + REDACTED


def test_nested_encoding_and_malformed_absolute_url_fail_closed():
    path = f"/signup/confirm/{TOKEN}/"
    for _ in range(8):
        path = quote(path, safe="")
    assert safe_url(path) == REDACTED
    assert safe_url(f"https://[invalid/{TOKEN}/") == REDACTED
    assert TOKEN not in safe_text("Invalid URI " + path)


@pytest.fixture
def gunicorn_logger():
    loggers = [logging.getLogger(name) for name in ("gunicorn.access", "gunicorn.error")]
    original = [(logger, logger.handlers[:], logger.filters[:], logger.level, logger.propagate) for logger in loggers]
    config = Config()
    config.set("accesslog", "-")
    logger = SafeLogger(config)
    stream = StringIO()
    for item in loggers:
        item.handlers = [logging.StreamHandler(stream)]
    try:
        yield logger, stream
    finally:
        for item, handlers, filters, level, propagate in original:
            item.handlers = handlers
            item.filters = filters
            item.setLevel(level)
            item.propagate = propagate


@pytest.mark.parametrize("custom_format,leading_slashes", [(False, "/"), (True, "//")])
def test_access_log_hides_secrets(gunicorn_logger, custom_format, leading_slashes):
    logger, stream = gunicorn_logger
    if custom_format:
        logger.cfg.set("access_log_format", "%(m)s %(U)s %(s)s %(q)s %(f)s %({referer}i)s %({missing}i)s")
    uri = quote(f"{leading_slashes}password-reset/{UID}/{TOKEN}/", safe="") + f"?token={QUERY}"
    referer = f"https://elsewhere.test/private/{REFERER}/?token={QUERY}"
    environ = {
        "REMOTE_ADDR": "127.0.0.1",
        "REQUEST_METHOD": "GET",
        "RAW_URI": uri,
        "REQUEST_URI": uri,
        "PATH_INFO": f"/password-reset/{UID}/{TOKEN}/",
        "QUERY_STRING": f"token={QUERY}",
        "HTTP_REFERER": referer,
        "SERVER_PROTOCOL": "HTTP/1.1",
    }
    request_headers = {"Referer": referer}
    response = SimpleNamespace(status="302 Found", sent=0, headers={"Location": uri})
    atoms = logger.atoms(response, request_headers, environ, timedelta())
    assert all(secret not in str(atoms) for secret in (TOKEN, UID, QUERY, REFERER))
    assert atoms["r"] == "GET /password-reset/[redacted]?[redacted] HTTP/1.1"
    assert atoms["s"] == "302"
    logger.access(response, request_headers, environ, timedelta())
    output = stream.getvalue()
    assert "GET /password-reset/" in output and "302" in output
    assert all(secret not in output for secret in (TOKEN, UID, QUERY, REFERER))
    assert environ["RAW_URI"] == uri and request_headers["Referer"] == referer


@pytest.mark.parametrize("malformed,prefix", [(False, "/signup/confirm"), (True, "//signup/confirm")])
def test_error_log_hides_secrets(gunicorn_logger, malformed, prefix):
    logger, stream = gunicorn_logger
    token = f"<invalid-{TOKEN}>" if malformed else TOKEN
    uri = f"{prefix}/{token}/?token={QUERY}"
    try:
        raise ValueError(f"Invalid URL {uri}")
    except ValueError:
        logger.exception("Error handling request %s %s", "GET", uri)
    output = stream.getvalue()
    assert TOKEN not in output and QUERY not in output
    assert "Error handling request GET /signup/confirm/" in output
    assert "ValueError" in output
    assert "Traceback" in output


@pytest.mark.parametrize("configuration", [LOGGING, deployment_logging_from_env({})], ids=["local", "production"])
@pytest.mark.parametrize(
    "logger_name,include_query",
    [("django.request", False), ("django.server", True), ("django.security.SuspiciousOperation", True)],
)
def test_django_log_hides_secrets(configuration, logger_name, include_query):
    logger = logging.getLogger(logger_name)
    original = (logger.handlers[:], logger.filters[:], logger.level, logger.propagate)
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    assert configuration["handlers"]["console"]["filters"] == ["sensitive_request"]
    assert configuration["filters"]["sensitive_request"]["()"] == "config.logging.SensitiveRequestFilter"
    for name in ("django", "django.server"):
        assert configuration["loggers"][name]["handlers"] == ["console"]
        assert not configuration["loggers"][name]["propagate"]
    handler.addFilter(SensitiveRequestFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s %(status_code)s %(message)s"))
    logger.handlers, logger.filters, logger.propagate = [handler], [], False
    logger.setLevel(logging.INFO)
    uri = f"/signup/confirm/{TOKEN}%20invalid/?token={QUERY}"
    request = RequestFactory().get(uri, HTTP_REFERER=f"https://example.test/{REFERER}/")
    original_path = request.path
    try:
        log_response(
            "Not Found: %s",
            request.get_full_path() if include_query else request.path,
            response=HttpResponse(status=404),
            request=request,
            logger=logger,
        )
    finally:
        logger.handlers, logger.filters, level, logger.propagate = original
        logger.setLevel(level)
    output = stream.getvalue()
    assert all(secret not in output for secret in (TOKEN, QUERY, REFERER))
    assert "WARNING 404 Not Found: /signup/confirm/" in output
    assert request.path == original_path
    assert request.META["QUERY_STRING"] == f"token={QUERY}"


@pytest.mark.parametrize("malformed,leading_slashes", [(False, "/"), (True, "//")])
def test_filter_copies_and_sanitizes_record(malformed, leading_slashes):
    token = f"<invalid-{TOKEN}>" if malformed else TOKEN
    uri = f"{leading_slashes}password-reset/{UID}/{token}/?token={QUERY}"
    record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Failed: %s", (uri,), None)
    record.exc_text = f"ValueError: {uri}"
    record.stack_info = f"Referenced {uri}"
    sanitized = SensitiveRequestFilter().filter(record)
    assert sanitized is not record
    assert record.args == (uri,)
    assert TOKEN in record.exc_text
    rendered = logging.Formatter("%(message)s").format(sanitized)
    assert all(secret not in rendered for secret in (TOKEN, UID, QUERY))
    assert "Failed: /password-reset/" in rendered
    assert REFERER not in safe_text(f"Referer: https://example.test/{REFERER}/")


@pytest.mark.parametrize("module", ["config.settings.local", "config.settings.prod"])
def test_gunicorn_filters_survive_django_startup(module, smtp_env, security_env):
    # Отдельный интерпретатор воспроизводит запуск worker без сервера и обращений к БД.
    script = """
import logging
from datetime import timedelta
from types import SimpleNamespace
from gunicorn.config import Config
from config.gunicorn import SafeLogger
from config.logging import SensitiveRequestFilter

config = Config()
config.set('accesslog', '-')
logger = SafeLogger(config)
import django
django.setup()
for item in (logger.access_log, logger.error_log):
    assert any(isinstance(f, SensitiveRequestFilter) for f in item.filters)
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils.log import log_response
uri = '/password-reset/synthetic-private-uid/synthetic-secret-token/?token=synthetic-query-value'
environ = {'REQUEST_METHOD': 'GET', 'RAW_URI': uri, 'PATH_INFO': uri.split('?')[0],
           'QUERY_STRING': 'token=synthetic-query-value', 'SERVER_PROTOCOL': 'HTTP/1.1',
           'HTTP_REFERER': 'https://example.test/synthetic-referrer-value/'}
logger.access(SimpleNamespace(status='404 Not Found', sent=0, headers=[]), {}, environ, timedelta())
logger.error('Error handling request %s', uri)
request = RequestFactory().get(uri)
log_response('Not Found: %s', request.path, response=HttpResponse(status=404), request=request)
logging.getLogger('django.server').warning('GET %s HTTP/1.1 404', uri)
"""
    result = subprocess.run(
        [os.sys.executable, "-c", script],
        env={
            **os.environ,
            **smtp_env,
            **security_env,
            "DJANGO_SETTINGS_MODULE": module,
            "DJANGO_SECRET_KEY": "logging-startup-test-key-that-is-more-than-fifty-characters",
            "DJANGO_ALLOWED_HOSTS": "localhost",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://localhost",
            "POSTGRES_DB": "unused_logging_test",
            "POSTGRES_USER": "unused_logging_test",
            "POSTGRES_PASSWORD": "unused-logging-test-password",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "5432",
            "POSTGRES_SSLMODE": "prefer",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, safe_text(output)
    assert all(secret not in output for secret in (TOKEN, UID, QUERY, REFERER))
    assert "GET /password-reset/[redacted]" in output and "404" in output
    assert "Error handling request" in output
    assert "Not Found" in output
