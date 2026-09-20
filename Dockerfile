FROM python:3.14-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements/ ./requirements/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements/base.txt

FROM python:3.14-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.local
WORKDIR /app
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .
RUN mkdir -p /app/staticfiles /app/media \
    && chown app:app /app/staticfiles /app/media \
    && chmod 0755 /app/staticfiles /app/media \
    && chmod +x /app/deploy/entrypoint.sh
USER 10001:10001
RUN DJANGO_SECRET_KEY=build-only-placeholder-longer-than-fifty-characters-2026 \
    python manage.py collectstatic --noinput
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/health/', headers={'Host': __import__('os').environ.get('HEALTHCHECK_HOST', 'localhost')}), timeout=3)" || exit 1
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "--logger-class", "config.gunicorn.SafeLogger", "--access-logfile", "-", "--error-logfile", "-"]
