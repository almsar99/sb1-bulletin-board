"""Публичная страница проекта и общая навигация."""

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_about_content(client):
    assert reverse("web:about") == "/about/"
    response = client.get("/about/")
    assert response.status_code == 200
    html = response.content.decode()
    for text in ["маркет.код", "market-kod.ru", "Django REST Framework", "PostgreSQL", "JWT", "дипломный проект"]:
        assert text in html
    assert "SB1" not in html
    article = html.split('<article class="about-page">')[1].split("</article>")[0]
    assert article.count("<section ") == 4
    assert re.findall(r'<h[12] id="[^"]+">([^<]+)</h[12]>', article) == [
        "О проекте",
        "Рубрики",
        "Как это работает",
        "Технологии",
    ]
    categories = article.split('aria-labelledby="about-categories"')[1].split("</section>")[0]
    for category in ("scripts", "freelance", "courses", "solutions", "hardware"):
        assert f'href="/category/{category}/"' in categories
    assert 'href="/discussions/"' in categories
    assert categories.count("<svg ") == 6
    lead = article.split('class="about-lead"')[1].split("</p>")[0]
    assert "маркет.код — доска объявлений на сайте" in lead
    assert "market-kod.ru" in lead


def test_about_footer_link(client):
    html = client.get("/about/").content.decode().split("<footer")[1]
    assert '<a href="/about/">О проекте</a>' in html
