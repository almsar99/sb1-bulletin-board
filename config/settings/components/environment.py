import ipaddress
import re
from urllib.parse import urlsplit


class EnvironmentConfigurationError(ValueError):
    """Ошибка при отсутствии обязательных или наличии небезопасных настроек развёртывания."""


def required_value(environ, name, *, preserve_whitespace=False):
    try:
        raw_value = environ[name]
    except KeyError as exc:
        raise EnvironmentConfigurationError(f"Required environment variable {name} is missing") from exc

    if not raw_value.strip():
        raise EnvironmentConfigurationError(f"Environment variable {name} must not be empty")
    return raw_value if preserve_whitespace else raw_value.strip()


def required_secret(environ, name, *, minimum_length=50):
    value = required_value(environ, name, preserve_whitespace=True)
    if len(value) < minimum_length:
        raise EnvironmentConfigurationError(
            f"Environment variable {name} must contain at least {minimum_length} characters"
        )
    return value


def boolean_value(environ, name, *, required=False, default=None):
    raw_value = environ.get(name)
    if raw_value is None:
        if required:
            raise EnvironmentConfigurationError(f"Required environment variable {name} is missing")
        return default

    normalized = raw_value.strip().lower()
    values = {
        "1": True,
        "true": True,
        "yes": True,
        "on": True,
        "0": False,
        "false": False,
        "no": False,
        "off": False,
    }
    try:
        return values[normalized]
    except KeyError as exc:
        raise EnvironmentConfigurationError(f"Environment variable {name} must be a boolean value") from exc


def integer_value(environ, name, *, required=False, default=None, minimum=None, maximum=None):
    raw_value = environ.get(name)
    if raw_value is None:
        if required:
            raise EnvironmentConfigurationError(f"Required environment variable {name} is missing")
        return default

    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise EnvironmentConfigurationError(f"Environment variable {name} must be an integer") from exc

    if minimum is not None and value < minimum:
        raise EnvironmentConfigurationError(f"Environment variable {name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise EnvironmentConfigurationError(f"Environment variable {name} must be at most {maximum}")
    return value


def choice_value(environ, name, choices, *, required=False, default=None):
    raw_value = environ.get(name)
    if raw_value is None:
        if required:
            raise EnvironmentConfigurationError(f"Required environment variable {name} is missing")
        return default

    value = raw_value.strip().lower()
    if value not in choices:
        supported = ", ".join(sorted(choices))
        raise EnvironmentConfigurationError(f"Environment variable {name} must be one of: {supported}")
    return value


def comma_separated_values(environ, name, *, required=False):
    raw_value = environ.get(name)
    if raw_value is None:
        if required:
            raise EnvironmentConfigurationError(f"Required environment variable {name} is missing")
        return []

    values = [value.strip() for value in raw_value.split(",")]
    if not values or any(not value for value in values):
        raise EnvironmentConfigurationError(f"Environment variable {name} must be a non-empty comma-separated list")
    if len(values) != len(set(values)):
        raise EnvironmentConfigurationError(f"Environment variable {name} must not contain duplicate values")
    return values


_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _is_valid_hostname(value):
    candidate = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    candidate = candidate[:-1] if candidate.endswith(".") else candidate
    if not candidate or len(candidate) > 253:
        return False
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return all(_DNS_LABEL.fullmatch(label) for label in candidate.split("."))
    return True


def allowed_hosts_from_env(environ):
    hosts = comma_separated_values(environ, "DJANGO_ALLOWED_HOSTS", required=True)
    for host in hosts:
        if host == "*" or host.startswith(".") or "://" in host or "/" in host:
            raise EnvironmentConfigurationError(
                "DJANGO_ALLOWED_HOSTS must contain only exact host names or IP addresses"
            )
        if not _is_valid_hostname(host):
            raise EnvironmentConfigurationError(f"DJANGO_ALLOWED_HOSTS contains an invalid host: {host}")
    return hosts


def trusted_origins_from_env(environ):
    origins = comma_separated_values(environ, "DJANGO_CSRF_TRUSTED_ORIGINS", required=True)
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise EnvironmentConfigurationError(
                f"DJANGO_CSRF_TRUSTED_ORIGINS contains an invalid origin: {origin}"
            ) from exc
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise EnvironmentConfigurationError("DJANGO_CSRF_TRUSTED_ORIGINS must contain exact HTTPS origins")
        if port is not None and not 1 <= port <= 65535:
            raise EnvironmentConfigurationError(f"DJANGO_CSRF_TRUSTED_ORIGINS contains an invalid port: {origin}")
        if not _is_valid_hostname(hostname):
            raise EnvironmentConfigurationError(f"DJANGO_CSRF_TRUSTED_ORIGINS contains an invalid host: {origin}")
    return origins
