"""Безопасные метаданные и рубричные заглушки."""

from html.parser import HTMLParser

import pytest
from django.template.loader import render_to_string

from ads.models import AdCategory

pytestmark = pytest.mark.django_db


class Metadata(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            self.values[attrs.get("name") or attrs.get("property")] = attrs.get("content")


def test_default_page_metadata(client, settings):
    settings.FRONTEND_URL = "https://market-kod.ru/"
    response = client.get("/?search=example")
    meta = Metadata(response.content.decode()).values
    assert meta["og:title"] == "маркет.код — доска объявлений"
    assert meta["og:url"] == "https://market-kod.ru/"
    assert meta["og:type"] == "website"
    assert meta["og:locale"] == "ru_RU"
    assert meta["twitter:card"] == "summary"
    assert meta["description"] == meta["og:description"]
    assert response.context["site_url"] == "https://market-kod.ru"


def test_ad_metadata_escaped_and_short(client, ad, settings):
    settings.FRONTEND_URL = "https://market-kod.ru"
    ad.title = 'Код "Python" & данные'
    ad.description = '<b>Описание</b> "пример" ' + "подробности " * 30
    ad.save()
    meta = Metadata(client.get(f"/ad/{ad.pk}/").content.decode()).values
    assert meta["og:title"] == ad.title + " — маркет.код"
    assert meta["og:url"] == f"https://market-kod.ru/ad/{ad.pk}/"
    assert meta["description"] == meta["og:description"]
    assert len(meta["description"]) == 160
    assert "<b>" not in meta["description"] and '"пример"' in meta["description"]


def test_placeholder_uses_category(client, ad):
    html = client.get(f"/ad/{ad.pk}/").content.decode().split('class="image-placeholder"')[1].split("</div>")[0]
    assert "Нет фото" in html and "<svg" in html
    assert "brand-symbol" not in html


def test_placeholder_brand_fallback(ad):
    ad.category = "unknown"
    html = render_to_string("includes/ad_card.html", {"ad": ad, "category_values": AdCategory.values})
    assert "brand-symbol" in html and "Нет фото" in html
