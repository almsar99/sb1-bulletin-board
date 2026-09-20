"""Права и полный цикл рассмотрения в формах, API и административной очереди."""

import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.test import RequestFactory
from django.utils import timezone

from ads.models import Ad, AdStatus
from web.context_processors import review_queue

pytestmark = pytest.mark.django_db
PAYLOAD = {"title": "Новая карточка", "price": 10, "description": "Описание", "category": "scripts"}


@pytest.fixture
def pending(ad):
    ad.status = AdStatus.DRAFT
    ad.submitted_at = timezone.now() - timedelta(days=1)
    ad.save()
    return ad


def test_site_create_pending(client, user):
    client.force_login(user)
    response = client.post("/ad/new/", PAYLOAD, follow=True)
    ad = Ad.objects.get()
    assert ad.status == AdStatus.DRAFT and ad.submitted_at
    assert (
        "Объявление отправлено на рассмотрение. В каталоге оно появится после подтверждения."
        in response.content.decode()
    )


@pytest.mark.parametrize("status", AdStatus.values)
def test_site_ignores_posted_status(client, user, status):
    client.force_login(user)
    assert client.post("/ad/new/", {**PAYLOAD, "status": status}).status_code == 302
    assert Ad.objects.get().status == AdStatus.DRAFT


def test_pending_hidden_from_catalog_and_category(client, pending, user):
    for account in (None, user):
        if account:
            client.force_login(account)
        assert not client.get("/").context["ads"]
        assert not client.get(f"/category/{pending.category}/").context["ads"]


def test_admin_publishes_into_catalog(client, admin, pending):
    client.force_login(admin)
    assert client.post(f"/ad/{pending.pk}/status/", {"status": "published"}).status_code == 302
    client.logout()
    assert list(client.get("/").context["ads"]) == [pending]


@pytest.mark.parametrize("status", AdStatus.values)
def test_owner_edit_resubmits(client, user, ad, status):
    ad.status = status
    ad.submitted_at = timezone.now() - timedelta(days=1)
    ad.save()
    previous = ad.submitted_at
    client.force_login(user)
    assert client.get(f"/ad/{ad.pk}/edit/").status_code == 200
    response = client.post(f"/ad/{ad.pk}/edit/", PAYLOAD, follow=True)
    ad.refresh_from_db()
    assert ad.title == PAYLOAD["title"]
    assert ad.status == AdStatus.DRAFT and ad.submitted_at > previous
    assert "Изменения сохранены и отправлены на рассмотрение." in response.content.decode()
    assert not client.get("/").context["ads"]


def test_owner_cannot_publish_web_or_api(client, user, user_client, pending):
    client.force_login(user)
    assert client.post(f"/ad/{pending.pk}/status/", {"status": "published"}).status_code == 403
    assert user_client.patch(f"/api/ads/{pending.pk}/", {"status": "published"}).status_code == 403
    assert user_client.patch(f"/api/ads/{pending.pk}/", {"status": "published", "title": "Обход"}).status_code == 403
    pending.refresh_from_db()
    assert pending.status == AdStatus.DRAFT


def test_admin_edit_preserves_publication(client, admin, ad):
    client.force_login(admin)
    assert client.get(f"/ad/{ad.pk}/edit/").status_code == 200
    assert client.post(f"/ad/{ad.pk}/edit/", PAYLOAD).status_code == 302
    ad.refresh_from_db()
    assert ad.title == PAYLOAD["title"]
    assert ad.status == AdStatus.PUBLISHED


def test_other_user_cannot_open_pending(client, other_user, other_client, pending):
    client.force_login(other_user)
    assert client.get(f"/ad/{pending.pk}/").status_code == 404
    assert other_client.get(f"/api/ads/{pending.pk}/reviews/").status_code == 404
    assert client.post(f"/ad/{pending.pk}/?kind=question", {"text": "Вопрос"}).status_code == 404


def test_owner_sees_pending_date_and_actions(client, user, pending):
    client.force_login(user)
    content = client.get(f"/ad/{pending.pk}/").content.decode()
    assert "Отправлено на рассмотрение" in content
    assert "Карточка на рассмотрении. В каталоге появится после подтверждения." in content
    assert 'value="published"' not in content and "Снять с публикации" not in content
    assert "Изменить" in content and "Удалить" in content


def test_admin_sees_other_pending(client, admin, pending):
    client.force_login(admin)
    response = client.get(f"/ad/{pending.pk}/")
    assert response.status_code == 200 and "Опубликовать" in response.content.decode()


def test_published_owner_has_no_publish_button(client, user, ad):
    client.force_login(user)
    content = client.get(f"/ad/{ad.pk}/").content.decode()
    assert 'value="published"' not in content and "Снять с публикации" in content


def test_queue_orders_all_authors_and_paginates(client, admin, user, other_user, pending):
    newest = []
    for number in range(5):
        newest.append(
            Ad.objects.create(
                author=other_user, **{**PAYLOAD, "title": str(number)}, status="draft", submitted_at=timezone.now()
            )
        )
    Ad.objects.create(author=user, **PAYLOAD)
    client.force_login(admin)
    response = client.get("/account/review/")
    assert response.status_code == 200
    assert list(response.context["ads"]) == list(reversed(newest))[:4]
    assert response.context["paginator"].count == 6
    assert pending in client.get("/account/review/?page=2").context["ads"]


def test_queue_denied_to_user_and_guest(client, user):
    assert client.get("/account/review/").status_code == 404
    client.force_login(user)
    assert client.get("/account/review/").status_code == 404


def test_counter_one_query_only_for_admin(admin, user, pending, django_assert_num_queries):
    request = RequestFactory().get("/")
    request.user = admin
    with django_assert_num_queries(1):
        assert review_queue(request) == {"review_queue_count": 1}
    for account in (user, AnonymousUser()):
        request.user = account
        with django_assert_num_queries(0):
            assert review_queue(request) == {}


def test_header_queue_only_admin(client, admin, user, pending):
    client.force_login(admin)
    assert 'href="/account/review/"' in client.get("/").content.decode()
    client.force_login(user)
    assert 'href="/account/review/"' not in client.get("/").content.decode()


def test_publication_email_once(admin_client, pending, settings, django_capture_on_commit_callbacks):
    settings.FRONTEND_URL = "https://market-kod.ru"
    settings.DEFAULT_FROM_EMAIL = "noreply@market-kod.ru"
    with django_capture_on_commit_callbacks(execute=True):
        for _ in range(2):
            assert admin_client.patch(f"/api/ads/{pending.pk}/", {"status": "published"}).status_code == 200
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == "Объявление опубликовано — маркет.код"
    assert message.from_email == "noreply@market-kod.ru" and message.to == [pending.author.email]
    assert pending.title in message.body and f"https://market-kod.ru/ad/{pending.pk}/" in message.body
    assert message.alternatives[0].mimetype == "text/html"


def test_admin_save_sends_no_email(admin_client, ad, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert admin_client.patch(f"/api/ads/{ad.pk}/", {"title": "Правка"}).status_code == 200
    assert not mail.outbox


def test_mail_failure_does_not_undo_publication(admin_client, pending, django_capture_on_commit_callbacks, caplog):
    with patch("ads.email.send_mail", side_effect=OSError("mail unavailable")):
        with django_capture_on_commit_callbacks(execute=True):
            assert admin_client.patch(f"/api/ads/{pending.pk}/", {"status": "published"}).status_code == 200
    pending.refresh_from_db()
    assert pending.status == AdStatus.PUBLISHED
    assert "mail unavailable" in caplog.text
    errors = [record for record in caplog.records if record.name == "ads.email"]
    assert len(errors) == 1
    assert errors[0].levelno == logging.ERROR
    assert errors[0].getMessage() == f"Не удалось отправить письмо о публикации объявления {pending.pk}"
    assert pending.title not in caplog.text
    assert pending.author.email not in caplog.text
    assert "Карточка появилась в каталоге" not in caplog.text


def test_republication_sends_new_email(admin_client, pending, django_capture_on_commit_callbacks):
    old_date = pending.submitted_at
    with django_capture_on_commit_callbacks(execute=True):
        for status in ("published", "draft", "published"):
            assert admin_client.patch(f"/api/ads/{pending.pk}/", {"status": status}).status_code == 200
    pending.refresh_from_db()
    assert pending.submitted_at > old_date and len(mail.outbox) == 2


def test_pending_copy_has_no_obsolete_terms(client, user, pending):
    client.force_login(user)
    for path in ("/account/", f"/ad/{pending.pk}/", "/ad/new/"):
        content = client.get(path).content.decode().lower()
        assert "на рассмотрении" in content or path == "/ad/new/"
        assert all(term not in content for term in ("модерация", "модератор", "черновик"))


def test_api_create_remains_published(user_client):
    response = user_client.post("/api/ads/", PAYLOAD)
    assert response.status_code == 201 and response.data["status"] == "published"
    assert response.data["submitted_at"]


def test_api_content_edit_resubmits(user_client, ad):
    assert user_client.patch(f"/api/ads/{ad.pk}/", {"title": "Правка"}).status_code == 200
    ad.refresh_from_db()
    assert ad.status == AdStatus.DRAFT and ad.submitted_at


@pytest.mark.parametrize("status", AdStatus.values)
@pytest.mark.parametrize("client_name", ["user_client", "admin_client"])
@pytest.mark.parametrize("payload_kind", ["empty", "read_only"])
def test_api_patch_without_writable_fields_preserves_ad(
    request, ad, other_user, status, client_name, payload_kind, mailoutbox, django_capture_on_commit_callbacks
):
    ad.status = status
    ad.submitted_at = timezone.now() - timedelta(days=1)
    ad.save(update_fields=["status", "submitted_at"])
    before = Ad.objects.filter(pk=ad.pk).values().get()
    payload = (
        {}
        if payload_kind == "empty"
        else {"author": other_user.pk, "created_at": timezone.now().isoformat(), "submitted_at": None}
    )
    client = request.getfixturevalue(client_name)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = client.patch(f"/api/ads/{ad.pk}/", payload, format="json")

    assert response.status_code == 200
    assert Ad.objects.filter(pk=ad.pk).values().get() == before
    assert not callbacks
    assert not mailoutbox


def test_guest_discussion_prompt(client, ad):
    assert "Войдите, чтобы оставить отзыв или вопрос." in client.get(f"/ad/{ad.pk}/").content.decode()


def test_clear_image_during_review_edit(client, user, ad):
    ad.image = "ads/old.png"
    ad.save()
    client.force_login(user)
    assert client.post(f"/ad/{ad.pk}/edit/", {**PAYLOAD, "image-clear": "on"}).status_code == 302
    ad.refresh_from_db()
    assert not ad.image and ad.status == AdStatus.DRAFT


def test_admin_web_create_is_pending(client, admin):
    client.force_login(admin)
    assert client.post("/ad/new/", {**PAYLOAD, "status": "published"}).status_code == 302
    assert Ad.objects.get().status == AdStatus.DRAFT
