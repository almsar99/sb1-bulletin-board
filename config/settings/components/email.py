from .environment import (
    EnvironmentConfigurationError,
    boolean_value,
    choice_value,
    integer_value,
    required_value,
)


def email_from_env(environ, *, allow_console):
    backend = choice_value(
        environ,
        "DJANGO_EMAIL_BACKEND",
        {"console", "smtp"},
        required=True,
    )
    if backend == "console":
        if not allow_console:
            raise EnvironmentConfigurationError("DJANGO_EMAIL_BACKEND=console is not allowed in production settings")
        return {"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"}

    use_tls = boolean_value(environ, "DJANGO_EMAIL_USE_TLS", required=True)
    use_ssl = boolean_value(environ, "DJANGO_EMAIL_USE_SSL", required=True)
    if use_tls == use_ssl:
        raise EnvironmentConfigurationError(
            "Exactly one of DJANGO_EMAIL_USE_TLS or DJANGO_EMAIL_USE_SSL must be enabled"
        )

    return {
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_HOST": required_value(environ, "DJANGO_EMAIL_HOST"),
        "EMAIL_PORT": integer_value(
            environ,
            "DJANGO_EMAIL_PORT",
            required=True,
            minimum=1,
            maximum=65535,
        ),
        "EMAIL_HOST_USER": required_value(environ, "DJANGO_EMAIL_HOST_USER"),
        "EMAIL_HOST_PASSWORD": required_value(
            environ,
            "DJANGO_EMAIL_HOST_PASSWORD",
            preserve_whitespace=True,
        ),
        "EMAIL_USE_TLS": use_tls,
        "EMAIL_USE_SSL": use_ssl,
        "EMAIL_TIMEOUT": integer_value(
            environ,
            "DJANGO_EMAIL_TIMEOUT",
            default=10,
            minimum=1,
        ),
        "DEFAULT_FROM_EMAIL": required_value(environ, "DJANGO_DEFAULT_FROM_EMAIL"),
        "SERVER_EMAIL": required_value(environ, "DJANGO_SERVER_EMAIL"),
    }


EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
