"""Вопросы, ответы, совместимость отзывов и закрытая лента."""

import pytest

from ads.models import Ad, Review

pytestmark = pytest.mark.django_db


@pytest.fixture
def question(ad, other_user):
    return Review.objects.create(ad=ad, author=other_user, kind="question", text="Есть документация?")


def url(ad):
    return f"/api/ads/{ad.pk}/reviews/"


def test_create_question_api(user_client, ad):
    response = user_client.post(url(ad), {"kind": "question", "text": "Вопрос"})
    assert response.status_code == 201
    assert Review.objects.get().kind == "question"


def test_create_question_web(client, user, ad):
    client.force_login(user)
    response = client.post(f"/ad/{ad.pk}/?kind=question", {"text": "Вопрос"})
    assert response.status_code == 302
    assert "?kind=question" in response.url
    assert Review.objects.get().kind == "question"


def test_filter_kind(user_client, ad, review, question):
    assert [x["id"] for x in user_client.get(url(ad)).data["results"]] == [review.pk]
    assert [x["id"] for x in user_client.get(url(ad), {"kind": "question"}).data["results"]] == [question.pk]


def test_question_tab_excludes_reviews(client, ad, review, question):
    response = client.get(f"/ad/{ad.pk}/?kind=question")
    assert list(response.context["reviews"]) == [question]
    assert review.text not in response.content.decode()


def test_review_tab_excludes_questions(client, ad, review, question):
    response = client.get(f"/ad/{ad.pk}/")
    assert list(response.context["reviews"]) == [review]
    assert question.text not in response.content.decode()


def test_answer_question_api(user_client, ad, question):
    response = user_client.post(url(ad), {"text": "Да, есть", "parent": question.pk})
    assert response.status_code == 201
    assert Review.objects.get(pk=response.data["id"]).parent == question
    assert user_client.get(url(ad)).data["count"] == 0
    detail = user_client.get(url(ad) + f"{question.pk}/")
    assert detail.data["answers"][0]["text"] == "Да, есть"


def test_answer_question_web(client, user, ad, question):
    client.force_login(user)
    response = client.post(f"/ad/{ad.pk}/?kind=question", {"text": "Да, есть", "parent": question.pk})
    assert response.status_code == 302
    assert question.answers.get().author == user
    assert "Да, есть" in client.get(response.url).content.decode()


def test_answer_review_rejected(user_client, client, user, ad, review):
    assert user_client.post(url(ad), {"text": "Ответ", "parent": review.pk}).status_code == 400
    client.force_login(user)
    response = client.post(f"/ad/{ad.pk}/?kind=question", {"text": "Ответ", "parent": review.pk})
    assert response.status_code == 200
    assert "parent" in response.context["review_form"].errors
    assert not review.answers.exists()


def test_answer_other_ad_rejected(user_client, client, user, ad, question):
    other = Ad.objects.create(author=user, title="Другой", price=1)
    assert user_client.post(url(other), {"text": "Ответ", "parent": question.pk}).status_code == 400
    client.force_login(user)
    response = client.post(f"/ad/{other.pk}/?kind=question", {"text": "Ответ", "parent": question.pk})
    assert "parent" in response.context["review_form"].errors
    assert not question.answers.exists()


def test_feed_requires_login(api_client, client):
    assert api_client.get("/api/discussions/").status_code == 401
    assert client.get("/discussions/").status_code == 302


def test_feed_authorized(user_client, client, user, question, review):
    response = user_client.get("/api/discussions/")
    assert response.status_code == 200
    assert [x["id"] for x in response.data["results"]] == [question.pk]
    assert response.data["results"][0]["ad_title"] == question.ad.title
    client.force_login(user)
    assert list(client.get("/discussions/").context["questions"]) == [question]


def test_feed_hides_foreign_drafts(user_client, client, user, question):
    question.ad.status = "draft"
    question.ad.author = question.author
    question.ad.save()
    assert user_client.get("/api/discussions/").data["count"] == 0
    client.force_login(user)
    assert not client.get("/discussions/").context["questions"]


def test_invalid_kind(user_client, client, ad):
    assert user_client.get(url(ad), {"kind": "invalid"}).status_code == 400
    assert client.get(f"/ad/{ad.pk}/?kind=invalid").status_code == 404
    assert user_client.post(url(ad), {"text": "Text", "kind": "invalid"}).status_code == 400


def test_cannot_answer_self(other_client, ad, question):
    assert other_client.patch(url(ad) + f"{question.pk}/", {"parent": question.pk}).status_code == 400


def test_cannot_turn_answered_question_into_review(other_client, ad, question, user):
    Review.objects.create(ad=ad, parent=question, author=user, text="Ответ")
    assert other_client.patch(url(ad) + f"{question.pk}/", {"kind": "review"}).status_code == 400
    question.refresh_from_db()
    assert question.kind == "question"


def test_foreign_question_mutation_denied(user_client, ad, question):
    assert user_client.patch(url(ad) + f"{question.pk}/", {"text": "Подмена"}).status_code == 403
    assert user_client.delete(url(ad) + f"{question.pk}/").status_code == 403


def test_feed_pagination(user_client, ad, user):
    for i in range(6):
        Review.objects.create(ad=ad, author=user, kind="question", text=str(i))
    response = user_client.get("/api/discussions/")
    assert response.data["count"] == 6 and len(response.data["results"]) == 4
    assert len(user_client.get("/api/discussions/?page=2").data["results"]) == 2
