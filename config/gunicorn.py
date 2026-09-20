"""Gunicorn сохраняет методы, маршруты и статусы без служебных токенов URL."""

from gunicorn.glogging import Logger

from config.logging import REDACTED, SensitiveRequestFilter, safe_text, safe_url


class SafeLogger(Logger):
    def setup(self, cfg):
        super().setup(cfg)
        for logger in (self.access_log, self.error_log):
            if not any(isinstance(item, SensitiveRequestFilter) for item in logger.filters):
                logger.addFilter(SensitiveRequestFilter())

    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key, value in atoms.items():
            if key in {"f", "{referer}i", "{http_referer}e"}:
                atoms[key] = REDACTED if value and value != "-" else "-"
            elif key in {"q", "{query_string}e"}:
                atoms[key] = REDACTED if value else ""
            elif key in {"U", "{raw_uri}e", "{request_uri}e", "{path_info}e"}:
                atoms[key] = safe_url(value) if value else value
            elif isinstance(value, str):
                atoms[key] = safe_text(value)
        atoms["r"] = f"{atoms['m']} {safe_url(environ['RAW_URI'])} {atoms['H']}"
        return atoms
