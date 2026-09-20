import os

from .environment import choice_value, integer_value, required_value


def database_from_env(environ, *, require_credentials):
    value = required_value if require_credentials else lambda source, name: source.get(name)

    name = value(environ, "POSTGRES_DB") or "bulletin_board"
    user = value(environ, "POSTGRES_USER") or "bulletin_board"
    password = (
        required_value(environ, "POSTGRES_PASSWORD", preserve_whitespace=True)
        if require_credentials
        else environ.get("POSTGRES_PASSWORD", "")
    )
    host = value(environ, "POSTGRES_HOST") or "127.0.0.1"
    port = integer_value(
        environ,
        "POSTGRES_PORT",
        required=require_credentials,
        default=5432,
        minimum=1,
        maximum=65535,
    )
    connection_max_age = integer_value(
        environ,
        "POSTGRES_CONN_MAX_AGE",
        default=60 if require_credentials else 0,
        minimum=0,
    )

    configuration = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": str(port),
        "CONN_MAX_AGE": connection_max_age,
        "CONN_HEALTH_CHECKS": require_credentials,
    }
    connect_timeout = integer_value(
        environ,
        "POSTGRES_CONNECT_TIMEOUT",
        default=2 if require_credentials else None,
        minimum=1,
        maximum=30,
    )
    options = {}
    if connect_timeout is not None:
        options["connect_timeout"] = connect_timeout
    ssl_mode = choice_value(
        environ,
        "POSTGRES_SSLMODE",
        {"allow", "disable", "prefer", "require", "verify-ca", "verify-full"},
        required=require_credentials,
    )
    if ssl_mode:
        options["sslmode"] = ssl_mode
    if options:
        configuration["OPTIONS"] = options
    return {"default": configuration}


DATABASES = database_from_env(os.environ, require_credentials=False)
