"""Бренд, палитра и ссылки писем без внешних ресурсов."""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.urls import resolve

from users.email import send_password_reset_email

pytestmark = pytest.mark.django_db


def test_pages_brand(client):
    html = client.get("/").content.decode()
    assert "маркет" in html and "код" in html and "market-kod.ru" in html
    assert "Рядом" not in html
    assert "brand-symbol" in html
    assert "data:image/svg+xml" in html


def test_hero_exact(client):
    html = client.get("/").content.decode()
    assert "<h1>Доска объявлений</h1>" in html
    assert "Скрипты, фриланс, курсы, готовые решения, железо, обсуждение" in html


def test_reset_uses_frontend_url(settings, user, mailoutbox):
    settings.FRONTEND_URL = "https://market-kod.ru/"
    settings.DEFAULT_FROM_EMAIL = "noreply@market-kod.ru"
    send_password_reset_email(user)
    message = mailoutbox[0]
    link = re.search(r"https://market-kod.ru/password-reset/\S+/", message.body).group()
    assert resolve(urlsplit(link).path).view_name == "web:password-reset-confirm"
    assert message.from_email == "noreply@market-kod.ru"
    assert message.alternatives[0].mimetype == "text/html"
    assert link in message.alternatives[0].content
    assert "Сменить пароль" in message.alternatives[0].content


def test_reset_custom_frontend(settings, user, mailoutbox):
    settings.FRONTEND_URL = "http://localhost:18100"
    send_password_reset_email(user)
    assert "http://localhost:18100/password-reset/" in mailoutbox[0].body


def test_palette_only_variables(settings):
    css = (Path(settings.BASE_DIR) / "static/css/site.css").read_text()
    variables, components = css.split("}", 1)
    assert set(re.findall(r"#[0-9a-fA-F]{6}", variables)) == {
        "#0B1220",
        "#111827",
        "#1F2A44",
        "#22D3EE",
        "#67E8F9",
        "#F8FAFC",
        "#94A3B8",
        "#F43F5E",
        "#34D399",
    }
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", components)
    assert "@import" not in css and "url(http" not in css


def test_admin_filters_search_and_actions(client, settings):
    from io import StringIO

    from django.core.management import call_command

    from ads.models import Ad
    from users.models import User

    settings.DEBUG = True
    call_command("seed_demo", stdout=StringIO())
    client.force_login(User.objects.get(email="demo-admin@market-kod.ru"))
    assert client.get("/admin/ads/ad/", {"status__exact": "draft", "category__exact": "hardware"}).status_code == 200
    assert client.get("/admin/users/user/", {"q": "demo-anna", "is_staff__exact": "0"}).status_code == 200
    ad = Ad.objects.filter(status="draft").first()
    response = client.post("/admin/ads/ad/", {"action": "publish", "_selected_action": [ad.pk]})
    assert response.status_code == 302
    ad.refresh_from_db()
    assert ad.status == "published"
    assert client.get(f"/admin/ads/ad/{ad.pk}/change/").status_code == 200


def test_deployment_review_checklist():
    content = (Path(__file__).resolve().parents[1] / "docs/DEPLOY.md").read_text()
    assert "## Чеклист публикации" in content
    assert all(
        item in content
        for item in (
            "DEBUG=False",
            "market-kod.ru,www.market-kod.ru",
            "DJANGO_CSRF_TRUSTED_ORIGINS=https://market-kod.ru",
            "FRONTEND_URL=https://market-kod.ru",
            "noreply@market-kod.ru",
            "seed_demo",
            "/account/review/",
            "карточка исчезает с главной",
        )
    )
