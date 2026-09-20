"""Вложенные отзывы и права авторов."""

import pytest

from ads.models import Ad, Review

pytestmark = pytest.mark.django_db


def url(ad, review=None):
    return f"/api/ads/{ad.pk}/reviews/" + (f"{review.pk}/" if review else "")


def test_create_assigns_author_and_ad(user_client, user, other_user, ad):
    other_ad = Ad.objects.create(title="Другой", price=5, author=other_user)
    response = user_client.post(url(ad), {"text": "Отзыв", "author": other_user.pk, "ad": other_ad.pk})
    assert response.status_code == 201
    created = Review.objects.get(pk=response.data["id"])
    assert created.author == user
    assert created.ad == ad
    assert created.kind == "review" and created.parent is None


def test_reviews_scoped_to_ad(user_client, ad, review, other_user):
    other_ad = Ad.objects.create(title="Другой", price=5, author=other_user)
    other_review = Review.objects.create(text="Чужой", author=other_user, ad=other_ad)
    response = user_client.get(url(ad))
    assert response.status_code == 200
    assert [item["id"] for item in response.data["results"]] == [review.pk]
    for method in ["get", "patch", "delete"]:
        assert getattr(user_client, method)(url(ad, other_review)).status_code == 404


def test_ad_owner_cannot_change_someone_elses_review(user_client, ad, other_user):
    review = Review.objects.create(text="Чужой", author=other_user, ad=ad)
    assert user_client.patch(url(ad, review), {"text": "Подмена"}).status_code == 403


def test_delete_ad_cascades(user_client, ad, review):
    assert user_client.delete(f"/api/ads/{ad.pk}/").status_code == 204
    assert not Review.objects.filter(pk=review.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_missing_parent_is_404(user_client, method):
    assert getattr(user_client, method)("/api/ads/999999/reviews/", {"text": "Текст"}).status_code == 404


def test_empty_review_rejected(user_client, ad):
    assert user_client.post(url(ad), {"text": "  "}).status_code == 400


def test_review_is_readable_by_other_user(other_client, ad, review):
    response = other_client.get(url(ad, review))
    assert response.status_code == 200
    assert response.data["text"] == review.text


def test_update_cannot_reassign_relations(user_client, ad, review, other_user):
    other_ad = Ad.objects.create(title="Другой", price=5, author=other_user)
    assert user_client.patch(url(ad, review), {"author": other_user.pk, "ad": other_ad.pk}).status_code == 200
    review.refresh_from_db()
    assert review.author_id == ad.author_id
    assert review.ad == ad


@pytest.fixture(params=["draft", "archived"])
def hidden_ad(request, ad, other_user):
    ad.author = other_user
    ad.status = request.param
    ad.save(update_fields=["author", "status"])
    return ad


@pytest.mark.parametrize("client_name", ["user_client", "admin_client"])
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_author_and_admin_access_review_after_ad_is_hidden(request, hidden_ad, review, client_name, method):
    client = request.getfixturevalue(client_name)
    response = getattr(client, method)(url(hidden_ad, review), {"text": "Исправленный отзыв"})

    assert response.status_code == (204 if method == "delete" else 200)
    if method == "delete":
        assert not Review.objects.filter(pk=review.pk).exists()
    else:
        review.refresh_from_db()
        assert response.data["id"] == review.pk
        assert review.text == ("Исправленный отзыв" if method == "patch" else "Отличное предложение")
    status = hidden_ad.status
    hidden_ad.refresh_from_db()
    assert hidden_ad.status == status


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_own_review_does_not_grant_access_to_foreign_hidden_review(user_client, hidden_ad, review, other_user, method):
    foreign = Review.objects.create(ad=hidden_ad, author=other_user, text="Скрытый чужой отзыв")
    response = getattr(user_client, method)(url(hidden_ad, foreign), {"text": "Подмена"})

    assert response.status_code == 404
    assert foreign.text not in str(response.data)
    foreign.refresh_from_db()
    assert foreign.text == "Скрытый чужой отзыв"


@pytest.mark.parametrize("method", ["get", "post"])
def test_own_review_does_not_open_hidden_ad_or_review_collection(user_client, hidden_ad, review, method):
    assert user_client.get(f"/api/ads/{hidden_ad.pk}/").status_code == 404
    assert getattr(user_client, method)(url(hidden_ad), {"text": "Новый отзыв"}).status_code == 404
    assert Review.objects.filter(ad=hidden_ad).count() == 1


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_hidden_ad_owner_cannot_modify_someone_elses_review(other_client, hidden_ad, review, method):
    assert getattr(other_client, method)(url(hidden_ad, review), {"text": "Подмена"}).status_code == 403
    review.refresh_from_db()
    assert review.text == "Отличное предложение"


@pytest.mark.parametrize("method", ["get", "patch"])
def test_own_hidden_question_does_not_expose_foreign_answers(user_client, hidden_ad, user, other_user, method):
    question = Review.objects.create(ad=hidden_ad, author=user, kind="question", text="Мой вопрос")
    own_answer = Review.objects.create(ad=hidden_ad, parent=question, author=user, text="Мой ответ")
    Review.objects.create(ad=hidden_ad, parent=question, author=other_user, text="Скрытый чужой ответ")

    response = getattr(user_client, method)(url(hidden_ad, question), {"text": "Уточнённый вопрос"})

    assert response.status_code == 200
    assert [answer["id"] for answer in response.data["answers"]] == [own_answer.pk]
    assert "Скрытый чужой ответ" not in str(response.data)


def test_hidden_review_cannot_be_attached_to_foreign_hidden_question(user_client, hidden_ad, review, other_user):
    question = Review.objects.create(ad=hidden_ad, author=other_user, kind="question", text="Скрытый вопрос")
    response = user_client.patch(url(hidden_ad, review), {"parent": question.pk})

    assert response.status_code == 400
    assert "parent" in response.data
    review.refresh_from_db()
    assert review.parent is None


@pytest.mark.parametrize("changed_field", ["kind", "parent"])
def test_hidden_foreign_answers_still_protect_question_structure(
    user_client, hidden_ad, user, other_user, changed_field
):
    question = Review.objects.create(ad=hidden_ad, author=user, kind="question", text="Мой вопрос")
    other_question = Review.objects.create(ad=hidden_ad, author=user, kind="question", text="Другой мой вопрос")
    answer = Review.objects.create(ad=hidden_ad, parent=question, author=other_user, text="Скрытый чужой ответ")
    assert user_client.get(url(hidden_ad, question)).data["answers"] == []

    payload = {"kind": "review"} if changed_field == "kind" else {"parent": other_question.pk}
    response = user_client.patch(url(hidden_ad, question), payload)

    assert response.status_code == 400
    assert "kind" in response.data
    assert answer.text not in str(response.data)
    question.refresh_from_db()
    assert question.kind == "question"
    assert question.parent_id is None
    assert Review.objects.filter(pk=answer.pk, parent=question).exists()


def test_own_hidden_review_is_still_scoped_to_its_ad(user_client, hidden_ad, review, other_user):
    another_ad = Ad.objects.create(author=other_user, title="Другое скрытое", price=5, status=hidden_ad.status)
    assert user_client.get(url(another_ad, review)).status_code == 404


def test_review_list_queries(user_client, ad, user, other_user, django_assert_num_queries):
    count = 4
    for number in range(count):
        question = Review.objects.create(ad=ad, author=user, kind="question", text=f"Вопрос {number}")
        Review.objects.create(ad=ad, parent=question, author=other_user, text=f"Ответ {number}")

    # Объявление, count, страница с авторами, ответы с авторами.
    with django_assert_num_queries(4):
        response = user_client.get(url(ad), {"kind": "question"})

    assert response.status_code == 200
    assert len(response.data["results"]) == count
    assert all(len(question["answers"]) == 1 for question in response.data["results"])
