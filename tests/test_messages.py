"""Private conversations, access boundaries, read receipts and demo cleanup."""

from io import StringIO

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ads.models import Ad
from messaging.context_processors import unread_messages
from messaging.models import Message, Thread
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def thread(ad, other_user):
    return Thread.objects.create(ad=ad, initiator=other_user)


def start(client, ad, text="Здравствуйте!"):
    return client.post(f"/ad/{ad.pk}/message/", {"text": text})


def count(user):
    request = RequestFactory().get("/")
    request.user = user
    return unread_messages(request).get("unread_message_count", 0)


def test_guest_cannot_start(client, ad):
    assert start(client, ad).status_code == 302
    assert not Thread.objects.exists()


def test_start_creates_thread_and_message(client, ad, other_user):
    client.force_login(other_user)
    response = start(client, ad)
    thread = Thread.objects.get()
    assert response.url == f"/messages/{thread.pk}/"
    assert thread.messages.get().author == other_user
    assert thread.other_participant(other_user) == ad.author
    assert thread.other_participant(ad.author) == other_user


def test_repeated_start_adds_message(client, ad, other_user):
    client.force_login(other_user)
    start(client, ad)
    start(client, ad, "Дополнение")
    assert Thread.objects.count() == 1
    assert Message.objects.count() == 2


def test_database_prevents_duplicate_thread(thread):
    with pytest.raises(IntegrityError), transaction.atomic():
        Thread.objects.create(ad=thread.ad, initiator=thread.initiator)


def test_author_replies(client, thread, user):
    client.force_login(user)
    old = thread.updated_at
    response = client.post(f"/messages/{thread.pk}/", {"text": "Добрый день"})
    thread.refresh_from_db()
    assert response.status_code == 302
    assert thread.messages.get().author == user
    assert thread.updated_at > old


@pytest.mark.parametrize("method", ["get", "post"])
def test_stranger_and_admin_get_404(client, thread, admin, method):
    client.force_login(admin)
    response = getattr(client, method)(f"/messages/{thread.pk}/", {"text": "Запрещено"})
    assert response.status_code == 404
    assert not Message.objects.exists()


def test_author_cannot_start_self(client, ad, user):
    client.force_login(user)
    assert start(client, ad).status_code == 404
    assert "Написать автору" not in client.get(f"/ad/{ad.pk}/").content.decode()


@pytest.mark.parametrize("text", ["", "   \n\t", "я" * 2001], ids=["empty", "whitespace", "too-long"])
def test_invalid_first_message_does_not_create_records(client, ad, other_user, text):
    client.force_login(other_user)
    response = start(client, ad, text)
    assert response.status_code == 200
    assert response.context["private_message_form"].errors
    assert not Thread.objects.exists() and not Message.objects.exists()
    assert not ad.reviews.exists()


def test_invalid_first_message_rechecks_ad_visibility(client, ad, other_user, monkeypatch):
    from messaging.forms import MessageForm

    original_is_valid = MessageForm.is_valid

    def hide_ad_after_validation(form):
        valid = original_is_valid(form)
        Ad.objects.filter(pk=ad.pk).update(status="draft")
        return valid

    client.force_login(other_user)
    monkeypatch.setattr(MessageForm, "is_valid", hide_ad_after_validation)

    response = start(client, ad, " ")

    assert response.status_code == 404
    assert ad.title not in response.content.decode()
    assert not Thread.objects.exists() and not Message.objects.exists()
    assert not ad.reviews.exists()


@pytest.mark.parametrize("text", ["", "   ", "я" * 2001], ids=["empty", "whitespace", "too-long"])
def test_model_rejects_invalid_message(thread, other_user, text):
    with pytest.raises(ValidationError):
        Message.objects.create(thread=thread, author=other_user, text=text)


def test_model_rejects_outsider(thread, admin):
    with pytest.raises(ValidationError):
        Message.objects.create(thread=thread, author=admin, text="Нет доступа")
    with pytest.raises(ValidationError):
        thread.other_participant(admin)


def test_thread_validation_rejects_self(ad, user):
    with pytest.raises(ValidationError):
        Thread(ad=ad, initiator=user).full_clean()


def test_length_boundary_and_strip(client, ad, other_user):
    client.force_login(other_user)
    assert start(client, ad, "я" * 2000).status_code == 302
    start(client, ad, "  Текст  ")
    assert Message.objects.last().text == "Текст"


def test_incoming_counter(thread, user, other_user, django_assert_num_queries):
    Message.objects.create(thread=thread, author=other_user, text="Входящее")
    with django_assert_num_queries(1):
        assert count(user) == 1
    assert count(other_user) == 0


def test_anonymous_counter_no_query(django_assert_num_queries):
    with django_assert_num_queries(0):
        assert count(AnonymousUser()) == 0


def test_read_only_marks_incoming(client, thread, user, other_user):
    incoming = Message.objects.create(thread=thread, author=other_user, text="Вопрос")
    own = Message.objects.create(thread=thread, author=user, text="Ответ")
    client.force_login(user)
    response = client.get(f"/messages/{thread.pk}/")
    incoming.refresh_from_db()
    own.refresh_from_db()
    assert response.status_code == 200 and incoming.read_at is not None
    assert own.read_at is None and count(user) == 0
    assert response.context["unread_message_count"] == 0
    stamp = incoming.read_at
    client.get(f"/messages/{thread.pk}/")
    incoming.refresh_from_db()
    assert incoming.read_at == stamp


def test_only_own_threads_in_list(client, thread, admin, other_user):
    Message.objects.create(thread=thread, author=other_user, text="Секретный вопрос")
    client.force_login(admin)
    assert not client.get("/messages/").context["rows"]
    client.force_login(other_user)
    response = client.get("/messages/")
    assert [r["thread"].pk for r in response.context["rows"]] == [thread.pk]
    assert "Секретный вопрос" in response.content.decode()


def test_list_order_by_latest_message(client, thread, user, other_user):
    second_ad = Ad.objects.create(author=user, title="Другое", price=1)
    second = Thread.objects.create(ad=second_ad, initiator=other_user)
    client.force_login(other_user)
    client.post(f"/messages/{thread.pk}/", {"text": "Новый ответ"})
    assert [r["thread"].pk for r in client.get("/messages/").context["rows"]] == [thread.pk, second.pk]


@pytest.fixture
def many_threads(user, other_user):
    ads = Ad.objects.bulk_create([Ad(author=user, title=f"Объявление {number}", price=1) for number in range(41)])
    threads = Thread.objects.bulk_create([Thread(ad=ad, initiator=other_user) for ad in ads])
    Thread.objects.filter(pk__in=[thread.pk for thread in threads]).update(updated_at=timezone.now())
    Message.objects.bulk_create(
        [Message(thread=thread, author=user, text=f"Вопрос {number}") for number, thread in enumerate(threads)]
    )
    return list(reversed(threads))


@pytest.mark.parametrize(
    ("requested_page", "number"),
    [(None, 1), ("1", 1), ("2", 2), ("3", 3), ("999", 3), ("0", 3), ("invalid", 1)],
)
def test_thread_pages(client, many_threads, other_user, admin, requested_page, number):
    hidden_ad = Ad.objects.create(author=admin, title="Чужой диалог", price=1)
    hidden = Thread.objects.create(ad=hidden_ad, initiator=many_threads[0].ad.author)
    client.force_login(other_user)
    response = client.get("/messages/", {} if requested_page is None else {"page": requested_page})
    page = response.context["page_obj"]
    rows = response.context["rows"]
    assert response.status_code == 200
    assert page.number == number
    assert page.paginator.count == 41
    assert page.paginator.num_pages == 3
    assert [row["thread"].pk for row in rows] == [
        thread.pk for thread in many_threads[(number - 1) * 20 : number * 20]
    ]
    assert all(row["thread"].pk != hidden.pk and row["thread"].has_unread for row in rows)
    html = response.content.decode()
    assert "Чужой диалог" not in html
    if page.has_previous():
        assert f'href="?page={number - 1}"' in html
    if page.has_next():
        assert f'href="?page={number + 1}"' in html
    assert count(other_user) == 41


def test_thread_list_limits_database_fetch_without_n_plus_one(client, many_threads, other_user):
    client.force_login(other_user)
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/messages/", {"page": 2})
    assert len(response.context["rows"]) == 20
    assert len(queries) <= 6
    selects = [query["sql"] for query in queries if query["sql"].startswith("SELECT")]
    assert any('FROM "messaging_thread"' in sql and "LIMIT 20 OFFSET 20" in sql for sql in selects)


@pytest.mark.parametrize(("total", "number"), [(0, 1), (50, 1), (51, 2), (101, 3)])
def test_latest_page(client, thread, user, total, number):
    messages = Message.objects.bulk_create(
        [Message(thread=thread, author=user, text=f"Сообщение {index}") for index in range(total)]
    )
    thread.messages.update(created_at=timezone.now())
    client.force_login(user)
    response = client.get(f"/messages/{thread.pk}/")
    assert response.status_code == 200
    assert response.context["page_obj"].number == number
    assert response.context["page_obj"].paginator.count == total
    assert [item.pk for item in response.context["items"]] == [item.pk for item in messages[(number - 1) * 50 :]]
    if number > 1:
        assert f'href="?page={number - 1}"' in response.content.decode()


@pytest.mark.parametrize(
    ("requested_page", "number"), [("1", 1), ("2", 2), ("3", 3), ("999", 3), ("0", 3), ("invalid", 1)]
)
def test_message_pages(client, thread, user, requested_page, number):
    messages = Message.objects.bulk_create(
        [Message(thread=thread, author=user, text=f"Сообщение {index}") for index in range(105)]
    )
    client.force_login(user)
    response = client.get(f"/messages/{thread.pk}/", {"page": requested_page})
    assert response.status_code == 200
    assert response.context["page_obj"].number == number
    assert [item.pk for item in response.context["items"]] == [
        item.pk for item in messages[(number - 1) * 50 : number * 50]
    ]


def test_pagination_marks_only_displayed_incoming_messages_read(client, thread, user, other_user):
    messages = Message.objects.bulk_create(
        [
            Message(thread=thread, author=user if index % 2 else other_user, text=f"Сообщение {index}")
            for index in range(101)
        ]
    )
    already_read_at = timezone.now()
    Message.objects.filter(pk=messages[0].pk).update(read_at=already_read_at)
    client.force_login(user)
    response = client.get(f"/messages/{thread.pk}/", {"page": 2})
    assert len(response.context["items"]) == 50
    assert response.context["unread_message_count"] == 25
    for index, message in enumerate(thread.messages.all()):
        if index == 0:
            assert message.read_at == already_read_at
        else:
            assert (message.read_at is not None) == (50 <= index < 100 and index % 2 == 0)
    response = client.get(f"/messages/{thread.pk}/")
    assert response.context["unread_message_count"] == 24
    response = client.get(f"/messages/{thread.pk}/", {"page": 1})
    assert response.context["unread_message_count"] == 0
    assert thread.messages.filter(author=user, read_at__isnull=False).count() == 0


def test_conversation_limits_database_fetch_without_n_plus_one(client, thread, user, other_user):
    Message.objects.bulk_create(
        [Message(thread=thread, author=other_user, text=f"Сообщение {index}") for index in range(101)]
    )
    client.force_login(user)
    with CaptureQueriesContext(connection) as queries:
        response = client.get(f"/messages/{thread.pk}/", {"page": 2})
    assert len(response.context["items"]) == 50
    assert len(queries) <= 8
    selects = [
        query["sql"]
        for query in queries
        if query["sql"].startswith("SELECT")
        and 'FROM "messaging_message"' in query["sql"]
        and "COUNT(" not in query["sql"]
    ]
    assert len(selects) == 1
    assert "LIMIT 50 OFFSET 50" in selects[0]


def test_reply_from_older_page_redirects_to_latest_messages(client, thread, user, other_user):
    Message.objects.bulk_create(
        [Message(thread=thread, author=other_user, text=f"История {index}") for index in range(100)]
    )
    client.force_login(user)
    response = client.post(f"/messages/{thread.pk}/?page=1", {"text": "Последний ответ"}, follow=True)
    assert response.redirect_chain == [(f"/messages/{thread.pk}/", 302)]
    assert response.context["page_obj"].number == 3
    assert [item.text for item in response.context["items"]] == ["Последний ответ"]
    assert response.context["unread_message_count"] == 100


def test_invalid_reply_stays_on_selected_page(client, thread, user):
    Message.objects.bulk_create([Message(thread=thread, author=user, text="История") for _ in range(100)])
    client.force_login(user)
    response = client.post(f"/messages/{thread.pk}/?page=2", {"text": " "})
    assert response.status_code == 200
    assert response.context["page_obj"].number == 2
    assert response.context["form"].errors
    assert len(response.context["items"]) == 50
    assert thread.messages.count() == 100


def test_private_page_404(client, thread, other_user, admin):
    Message.objects.bulk_create(
        [Message(thread=thread, author=other_user, text=f"Секрет {index}") for index in range(101)]
    )
    client.force_login(admin)
    response = client.get(f"/messages/{thread.pk}/", {"page": 2})
    assert response.status_code == 404
    assert not thread.messages.filter(read_at__isnull=False).exists()


def test_delete_ad_cascades(thread, other_user):
    Message.objects.create(thread=thread, author=other_user, text="Удаляется")
    thread.ad.delete()
    assert not Thread.objects.exists() and not Message.objects.exists()


@pytest.mark.parametrize("participant", ["author", "initiator"])
def test_delete_account_cascades(thread, other_user, participant):
    Message.objects.create(thread=thread, author=other_user, text="Удаляется")
    (thread.ad.author if participant == "author" else thread.initiator).delete()
    assert not Thread.objects.exists() and not Message.objects.exists()


@pytest.mark.parametrize("status", ["archived", "draft"])
def test_existing_conversation_survives_unpublished_ad(client, thread, other_user, status):
    thread.ad.status = status
    thread.ad.save()
    client.force_login(other_user)
    assert client.get(f"/ad/{thread.ad_id}/").status_code == 404
    assert client.get(f"/messages/{thread.pk}/").status_code == 200
    assert client.post(f"/messages/{thread.pk}/", {"text": "Продолжаем"}).status_code == 302
    assert start(client, thread.ad).status_code == 404


@pytest.mark.parametrize("status", ["archived", "draft"])
def test_admin_cannot_start_on_unpublished_ad(client, ad, admin, status):
    ad.status = status
    ad.save()
    client.force_login(admin)
    assert start(client, ad).status_code == 404


def test_header_link_and_guest_prompt(client, ad, other_user):
    html = client.get(f"/ad/{ad.pk}/").content.decode()
    assert 'href="/messages/"' not in html
    assert "Войдите, чтобы написать автору" in html
    client.force_login(other_user)
    html = client.get(f"/ad/{ad.pk}/").content.decode()
    assert 'href="/messages/"' in html and "Написать автору" in html
    start(client, ad)
    assert "Перейти к переписке" in client.get(f"/ad/{ad.pk}/").content.decode()


def test_admin_separate_counters(client, ad, admin, other_user):
    ad.author = admin
    ad.save()
    Ad.objects.create(title="Черновик", price=1, author=other_user, status="draft")
    thread = Thread.objects.create(ad=ad, initiator=other_user)
    Message.objects.create(thread=thread, author=other_user, text="Раз")
    Message.objects.create(thread=thread, author=other_user, text="Два")
    client.force_login(admin)
    response = client.get("/")
    assert response.context["review_queue_count"] == 1
    assert response.context["unread_message_count"] == 2
    assert 'href="/account/review/"' in response.content.decode()
    assert 'href="/messages/"' in response.content.decode()


def test_start_post_only_and_csrf(client, ad, other_user):
    from django.test import Client

    client.force_login(other_user)
    assert client.get(f"/ad/{ad.pk}/message/").status_code == 405
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(other_user)
    assert start(csrf_client, ad).status_code == 403


def test_admin_models_read_only(thread, admin):
    request = RequestFactory().get("/admin/")
    request.user = admin
    for model in (Thread, Message):
        config = django_admin.site._registry[model]
        assert not config.has_add_permission(request)
        assert not config.has_change_permission(request)
        assert not config.has_delete_permission(request)
        assert "id" in config.get_readonly_fields(request)


def test_message_text_is_escaped_and_no_mail(client, thread, other_user, mailoutbox):
    client.force_login(other_user)
    response = client.post(f"/messages/{thread.pk}/", {"text": "<script>alert(1)</script>"}, follow=True)
    assert "&lt;script&gt;" in response.content.decode()
    assert not mailoutbox


def test_seed_threads_idempotent_and_flush(settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    call_command("seed_demo", stdout=StringIO())
    assert Thread.objects.count() == 3 and Message.objects.count() == 9
    assert Message.objects.filter(read_at=None).exists()
    before = list(Message.objects.values_list("id", "read_at"))
    call_command("seed_demo", stdout=StringIO())
    assert list(Message.objects.values_list("id", "read_at")) == before
    call_command("seed_demo", flush=True, stdout=StringIO())
    assert not Thread.objects.exists() and not Message.objects.exists()


def test_seed_flush_preserves_real_conversation(settings, tmp_path, user):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    call_command("seed_demo", stdout=StringIO())
    thread = Thread.objects.first()
    real = Message.objects.create(thread=thread, author=thread.initiator, text="Настоящее сообщение")
    call_command("seed_demo", flush=True, stdout=StringIO())
    assert Message.objects.filter(pk=real.pk).exists()
    assert Thread.objects.filter(pk=thread.pk).exists()
    real.delete()
    call_command("seed_demo", flush=True, stdout=StringIO())
    assert not Thread.objects.exists()
    assert list(User.objects.all()) == [user]


def test_guest_private_pages_redirect(client, thread):
    assert client.get("/messages/").status_code == 302
    assert client.get(f"/messages/{thread.pk}/").status_code == 302


def test_contact_form_has_distinct_ids(client, ad, other_user):
    import re

    client.force_login(other_user)
    html = client.get(f"/ad/{ad.pk}/").content.decode()
    ids = re.findall(r' id="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    assert 'for="private_text"' in html


def test_rate_eleventh_message_rejected(client, thread, other_user):
    client.force_login(other_user)
    for number in range(10):
        assert client.post(f"/messages/{thread.pk}/", {"text": f"Сообщение {number}"}).status_code == 302
    response = client.post(f"/messages/{thread.pk}/", {"text": "Сохранить этот текст"})
    assert response.status_code == 200
    assert "Слишком часто. Подождите минуту." in response.content.decode()
    assert response.context["form"]["text"].value() == "Сохранить этот текст"
    assert thread.messages.count() == 10


def test_rate_window_expires_without_extension(client, thread, other_user):
    import time
    from unittest.mock import patch

    client.force_login(other_user)
    clock = time.time()
    with patch("django.core.cache.backends.locmem.time.time", return_value=clock):
        client.post(f"/messages/{thread.pk}/", {"text": "Первое"})
    with patch("django.core.cache.backends.locmem.time.time", return_value=clock + 50):
        for _ in range(9):
            client.post(f"/messages/{thread.pk}/", {"text": "Позднее"})
        assert client.post(f"/messages/{thread.pk}/", {"text": "Отказ"}).status_code == 200
    with patch("django.core.cache.backends.locmem.time.time", return_value=clock + 61):
        assert client.post(f"/messages/{thread.pk}/", {"text": "Новое окно"}).status_code == 302
    assert thread.messages.count() == 11


def test_rate_second_thread_rejected_with_text(client, ad, user, other_user):
    client.force_login(other_user)
    assert start(client, ad).status_code == 302
    second = Ad.objects.create(author=user, title="Другое", price=1)
    response = start(client, second, "Мой вопрос сохранён")
    assert response.status_code == 200
    assert "Слишком часто. Подождите минуту." in response.content.decode()
    assert response.context["private_message_form"]["text"].value() == "Мой вопрос сохранён"
    assert not response.context["review_form"].is_bound
    assert not second.reviews.exists()
    assert Thread.objects.count() == 1
    # The thread quota does not prevent sending into the existing conversation.
    assert start(client, ad, "Дополнение").status_code == 302


def test_rate_thread_window_expires(client, ad, user, other_user):
    import time
    from unittest.mock import patch

    client.force_login(other_user)
    clock = time.time()
    with patch("django.core.cache.backends.locmem.time.time", return_value=clock):
        assert start(client, ad).status_code == 302
    second = Ad.objects.create(author=user, title="Следующее", price=1)
    with patch("django.core.cache.backends.locmem.time.time", return_value=clock + 31):
        assert start(client, second).status_code == 302
    assert Thread.objects.count() == 2


def test_rate_separate_users(client, thread, other_user, user, admin):
    client.force_login(other_user)
    for _ in range(10):
        client.post(f"/messages/{thread.pk}/", {"text": "Текст"})
    client.force_login(user)
    assert client.post(f"/messages/{thread.pk}/", {"text": "Ответ"}).status_code == 302
    client.force_login(admin)
    assert start(client, thread.ad).status_code == 302


def test_rate_shared_between_start_and_reply(client, ad, other_user):
    client.force_login(other_user)
    assert start(client, ad).status_code == 302
    thread = Thread.objects.get()
    for _ in range(9):
        client.post(f"/messages/{thread.pk}/", {"text": "Ответ"})
    response = start(client, ad, "Ещё один")
    assert response.status_code == 200
    assert thread.messages.count() == 10


def test_rate_invalid_text_does_not_consume_quota(client, ad, other_user):
    client.force_login(other_user)
    for _ in range(12):
        start(client, ad, " ")
    assert start(client, ad).status_code == 302


def test_rate_cache_expiry_between_add_and_increment():
    from unittest.mock import patch

    from messaging.rate_limit import allow_send

    with patch("messaging.rate_limit.cache") as cache:
        cache.get.return_value = 0
        cache.add.side_effect = [False, True]
        cache.incr.side_effect = ValueError("expired")
        assert allow_send(123)
        assert cache.add.call_count == 2
        cache.add.assert_called_with("messaging:send:123", 1, timeout=60)


def test_rate_cache_configuration():
    from django.core.exceptions import ImproperlyConfigured

    from config.settings.components.cache import cache_from_env

    options = {"MAX_ENTRIES": 50000, "CULL_FREQUENCY": 10}
    default = cache_from_env({})
    assert default["BACKEND"].endswith("LocMemCache")
    assert default["OPTIONS"] == options
    local = cache_from_env({"DJANGO_CACHE_URL": "locmem://custom"})
    assert local["LOCATION"] == "custom"
    assert local["OPTIONS"] == options
    assert cache_from_env({"DJANGO_CACHE_URL": "database://shared_cache"}) == {
        "BACKEND": "messaging.cache.FixedExpiryDatabaseCache",
        "LOCATION": "shared_cache",
        "OPTIONS": options,
    }
    with pytest.raises(ImproperlyConfigured):
        cache_from_env({"DJANGO_CACHE_URL": "unknown://host"})


def test_rate_database_cache_keeps_expiration(settings):
    from django.core.cache import caches
    from django.db import connection

    from messaging.rate_limit import allow_send

    previous = settings.CACHES
    settings.CACHES = {
        "default": {"BACKEND": "messaging.cache.FixedExpiryDatabaseCache", "LOCATION": "test_message_rate_cache"}
    }
    call_command("createcachetable", stdout=StringIO())
    cache = caches["default"]
    try:
        assert allow_send(123)
        with connection.cursor() as cursor:
            cursor.execute("SELECT expires FROM test_message_rate_cache")
            expires = cursor.fetchone()[0]
        for _ in range(9):
            assert allow_send(123)
        assert not allow_send(123)
        with connection.cursor() as cursor:
            cursor.execute("SELECT expires FROM test_message_rate_cache")
            assert cursor.fetchone()[0] == expires
        cache.clear()
        with pytest.raises(ValueError):
            cache.incr("missing")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE test_message_rate_cache")
        settings.CACHES = previous
