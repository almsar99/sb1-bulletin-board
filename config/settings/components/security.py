from django.utils.csp import CSP

from .environment import (
    EnvironmentConfigurationError,
    boolean_value,
    integer_value,
)

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"


def deployment_security_from_env(environ):
    hsts_seconds = integer_value(
        environ,
        "DJANGO_SECURE_HSTS_SECONDS",
        required=True,
        minimum=0,
    )
    hsts_subdomains = boolean_value(
        environ,
        "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
        required=True,
    )
    hsts_preload = boolean_value(
        environ,
        "DJANGO_SECURE_HSTS_PRELOAD",
        required=True,
    )
    if hsts_seconds == 0 and (hsts_subdomains or hsts_preload):
        raise EnvironmentConfigurationError("HSTS subdomain and preload flags require a positive HSTS duration")
    if hsts_preload and (not hsts_subdomains or hsts_seconds < 31_536_000):
        raise EnvironmentConfigurationError(
            "HSTS preload requires includeSubDomains and a duration of at least one year"
        )

    behind_tls_proxy = boolean_value(
        environ,
        "DJANGO_BEHIND_TLS_PROXY",
        required=True,
    )
    return {
        "CSRF_COOKIE_HTTPONLY": True,
        "CSRF_COOKIE_SAMESITE": "Lax",
        "CSRF_COOKIE_SECURE": True,
        "SECURE_CONTENT_TYPE_NOSNIFF": True,
        "SECURE_CSP": {
            "base-uri": [CSP.SELF],
            "connect-src": [CSP.SELF],
            "default-src": [CSP.SELF],
            "font-src": [CSP.SELF, "data:"],
            "form-action": [CSP.SELF],
            "frame-ancestors": [CSP.NONE],
            "img-src": [CSP.SELF, "data:"],
            "object-src": [CSP.NONE],
            "script-src": [CSP.SELF, CSP.UNSAFE_INLINE],
            "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
        },
        "SECURE_CROSS_ORIGIN_OPENER_POLICY": "same-origin",
        "SECURE_HSTS_INCLUDE_SUBDOMAINS": hsts_subdomains,
        "SECURE_HSTS_PRELOAD": hsts_preload,
        "SECURE_HSTS_SECONDS": hsts_seconds,
        "SECURE_PROXY_SSL_HEADER": (("HTTP_X_FORWARDED_PROTO", "https") if behind_tls_proxy else None),
        "SECURE_REDIRECT_EXEMPT": [r"^health(?:/(?:live|ready))?/$"],
        "SECURE_REFERRER_POLICY": "same-origin",
        "SECURE_SSL_REDIRECT": True,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": True,
        "USE_X_FORWARDED_HOST": False,
        "USE_X_FORWARDED_PORT": False,
        "X_FRAME_OPTIONS": "DENY",
    }
