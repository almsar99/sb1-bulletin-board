from .environment import choice_value

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"sensitive_request": {"()": "config.logging.SensitiveRequestFilter"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["sensitive_request"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        name: {"handlers": ["console"], "level": "INFO", "propagate": False} for name in ("django", "django.server")
    },
}


def deployment_logging_from_env(environ):
    level = choice_value(
        environ,
        "DJANGO_LOG_LEVEL",
        {"critical", "error", "warning", "info"},
        default="info",
    ).upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"sensitive_request": {"()": "config.logging.SensitiveRequestFilter"}},
        "formatters": {
            "standard": {
                "format": "{asctime} {levelname} {name}: {message}",
                "style": "{",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "filters": ["sensitive_request"],
            },
        },
        "root": {
            "handlers": ["console"],
            "level": level,
        },
        "loggers": {
            name: {
                "handlers": ["console"],
                "level": level,
                "propagate": False,
            }
            for name in ("django", "django.server")
        },
    }
