"""Страницы, сессии, права владельца, изображения и восстановление пароля."""

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import check_password
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from ads.models import Ad, Review
from tests.conftest import PASSWORD
from tests.test_ad_images import image_file
from users.models import SignupRequest, User
from users.tokens import make_reset_credentials
from web.views import server_error_view

pytestmark = pytest.mark.django_db
DATA = {
    "status": "published",
    "category": "scripts",
    "title": "Дубовый стол",
    "price": 5400,
    "description": "Крепкий стол, состояние хорошее.",
}


@pytest.fixture(autouse=True)
def web_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def web_client(client, user):
    client.force_login(user)
    return client


def detail(ad):
    return reverse("web:ad-detail", args=[ad.pk])


def test_home_public(client, ad):
    response = client.get("/")
    assert response.status_code == 200
    assert ad.title in response.content.decode()
    assert response.context["ads"][0] == ad


def test_search_substring_case_insensitive(client, user):
    found = Ad.objects.create(**DATA, author=user)
    Ad.objects.create(title="Лампа", price=200, description="Настольная", author=user)
    response = client.get("/", {"search": "ДУБОВ"})
    assert list(response.context["ads"]) == [found]


def test_search_with_pagination(client, user):
    ads = [Ad.objects.create(**DATA, author=user) for _ in range(7)]
    first = client.get("/", {"search": "стол", "page": 1})
    second = client.get("/", {"search": "стол", "page": 2})
    assert len(first.context["ads"]) == 4
    assert len(second.context["ads"]) == 3
    assert list(first.context["ads"]) + list(second.context["ads"]) == ads[::-1]
    assert "search=" in first.content.decode() and "page=2" in first.content.decode()


def test_empty_and_unmatched_list(client, ad):
    response = client.get("/", {"search": "несуществующее"})
    assert "Пока ничего не нашлось" in response.content.decode()
    assert not response.context["ads"]


def test_public_detail_and_reviews(client, ad, review):
    response = client.get(detail(ad))
    assert response.status_code == 200
    assert review.text in response.content.decode()
    assert response.context["review_form"] is None
    assert 'name="text"' not in response.content.decode()


def test_authenticated_adds_review(web_client, ad, user, other_user):
    response = web_client.post(detail(ad), {"text": "Спасибо за предложение", "author": other_user.pk})
    assert response.status_code == 302
    assert response.url == detail(ad) + "#reviews"
    review = Review.objects.get()
    assert review.author == user and review.ad == ad


def test_anonymous_cannot_add_review(client, ad):
    assert client.post(detail(ad), {"text": "Текст"}).status_code == 302
    assert not Review.objects.exists()


def test_empty_review_errors(web_client, ad):
    response = web_client.post(detail(ad), {"text": " "})
    assert response.status_code == 200
    assert response.context["review_form"].errors
    assert not Review.objects.exists()


@pytest.mark.parametrize("url", ["/ad/new/", "/account/"])
def test_private_pages_redirect_to_login(client, url):
    response = client.get(url)
    assert response.status_code == 302
    assert response.url.startswith("/login/?next=")


def test_create_ad_sets_owner(web_client, user, other_user):
    response = web_client.post("/ad/new/", {**DATA, "author": other_user.pk})
    assert response.status_code == 302
    ad = Ad.objects.get()
    assert ad.author == user
    assert ad.category == DATA["category"]
    assert response.url == detail(ad)


def test_form_accepts_photo(web_client):
    response = web_client.post("/ad/new/", {**DATA, "image": image_file()})
    assert response.status_code == 302
    assert Ad.objects.get().image


def test_form_rejects_misleading_photo(web_client):
    response = web_client.post("/ad/new/", {**DATA, "image": image_file("photo.jpg", "PNG")})
    assert response.status_code == 200
    assert "image" in response.context["form"].errors
    assert not Ad.objects.exists()


def test_form_rejects_invalid_values(web_client):
    payload = {**DATA, "price": -1, "title": ""}
    payload.pop("category")
    response = web_client.post("/ad/new/", payload)
    assert response.status_code == 200
    assert {"price", "title", "category"} <= response.context["form"].errors.keys()
    assert not Ad.objects.exists()


@pytest.mark.parametrize("action", ["ad-edit", "ad-delete"])
@pytest.mark.parametrize("method", ["get", "post"])
def test_other_user_forbidden(client, other_user, ad, action, method):
    client.force_login(other_user)
    response = getattr(client, method)(reverse("web:" + action, args=[ad.pk]), DATA)
    assert response.status_code == 403
    ad.refresh_from_db()
    assert ad.title == "Велосипед"


def test_author_delete_requires_post(web_client, ad, review):
    url = reverse("web:ad-delete", args=[ad.pk])
    assert web_client.get(url).status_code == 200
    assert Ad.objects.filter(pk=ad.pk).exists()
    assert web_client.post(url).status_code == 302
    assert not Ad.objects.exists() and not Review.objects.exists()


@pytest.mark.parametrize("role", ["user", "admin"])
def test_author_or_admin_deletes_review(client, request, role, ad, review):
    client.force_login(request.getfixturevalue(role))
    url = reverse("web:review-delete", args=[review.pk])
    assert client.get(url).status_code == 405
    assert client.post(url).status_code == 302
    assert not Review.objects.exists()


def test_ad_owner_cannot_delete_others_review(web_client, ad, other_user):
    review = Review.objects.create(ad=ad, author=other_user, text="Чужой отзыв")
    assert web_client.post(reverse("web:review-delete", args=[review.pk])).status_code == 403
    assert Review.objects.filter(pk=review.pk).exists()


@pytest.fixture(params=["draft", "archived"])
def hidden_review_ad(request, ad, other_user):
    ad.author = other_user
    ad.status = request.param
    ad.save(update_fields=["author", "status"])
    return ad


@pytest.mark.parametrize("actor", ["user", "admin"])
def test_author_or_admin_deletes_review_of_hidden_ad(client, request, hidden_review_ad, review, other_user, actor):
    client.force_login(request.getfixturevalue(actor))
    foreign = Review.objects.create(ad=hidden_review_ad, author=other_user, text="Скрытый чужой отзыв")
    url = reverse("web:review-delete", args=[review.pk])
    assert client.get(url).status_code == 405
    assert Review.objects.filter(pk=review.pk).exists()

    response = client.post(url, follow=True)

    destination = reverse("web:ad-list") if actor == "user" else detail(hidden_review_ad) + "#reviews"
    assert response.redirect_chain == [(destination, 302)]
    assert response.status_code == 200
    assert not Review.objects.filter(pk=review.pk).exists()
    assert Review.objects.filter(pk=foreign.pk).exists()
    if actor == "user":
        assert hidden_review_ad.title not in response.content.decode()
        assert foreign.text not in response.content.decode()
        assert client.get(detail(hidden_review_ad)).status_code == 404
    status = hidden_review_ad.status
    hidden_review_ad.refresh_from_db()
    assert hidden_review_ad.status == status


def test_own_hidden_review_does_not_allow_deleting_foreign_review(web_client, hidden_review_ad, review, other_user):
    foreign = Review.objects.create(ad=hidden_review_ad, author=other_user, text="Скрытый чужой отзыв")

    response = web_client.post(reverse("web:review-delete", args=[foreign.pk]))

    assert response.status_code == 404
    assert foreign.text not in response.content.decode()
    assert hidden_review_ad.title not in response.content.decode()
    assert Review.objects.filter(pk__in=[review.pk, foreign.pk]).count() == 2


def test_hidden_ad_owner_cannot_delete_foreign_review(client, hidden_review_ad, review, other_user):
    client.force_login(other_user)

    response = client.post(reverse("web:review-delete", args=[review.pk]))

    assert response.status_code == 403
    assert Review.objects.filter(pk=review.pk).exists()


def test_delete_own_hidden_review_requires_csrf(user, hidden_review_ad, review):
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(user)
    url = reverse("web:review-delete", args=[review.pk])
    assert browser.post(url).status_code == 403
    assert Review.objects.filter(pk=review.pk).exists()
    browser.get(reverse("web:ad-list"))

    response = browser.post(url, {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}, follow=True)

    assert response.redirect_chain == [(reverse("web:ad-list"), 302)]
    assert response.status_code == 200
    assert not Review.objects.filter(pk=review.pk).exists()


def test_login_success_and_case_normalization(client, user):
    response = client.post("/login/", {"username": user.email.upper(), "password": PASSWORD})
    assert response.status_code == 302 and response.url == "/"
    assert int(client.session["_auth_user_id"]) == user.pk


def test_wrong_login_has_error(client, user):
    response = client.post("/login/", {"username": user.email, "password": "wrong"})
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert "_auth_user_id" not in client.session


def test_login_next_cannot_leave_site(client, user):
    response = client.post(
        "/login/", {"username": user.email, "password": PASSWORD, "next": "https://untrusted.example/"}
    )
    assert response.url == "/"


def test_signup_creates_request_and_shows_check_email_page(client, mailoutbox):
    data = {
        "email": "New@Example.com",
        "first_name": "Мария",
        "last_name": "Иванова",
        "phone": "+79990000123",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
        "role": "admin",
    }
    response = client.post("/signup/", data)
    assert response.status_code == 302
    assert response.url == "/signup/check-email/"
    signup = SignupRequest.objects.get(email="new@example.com")
    assert check_password(PASSWORD, signup.password)
    assert signup.first_name == "Мария" and signup.last_name == "Иванова"
    assert signup.phone == "+79990000123"
    assert not User.objects.exists()
    assert "_auth_user_id" not in client.session
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [signup.email]
    assert f"/signup/confirm/{signup.token}/" in mailoutbox[0].body

    check_email_page = client.get(response.url)
    assert check_email_page.status_code == 200
    assert "Проверьте почту" in check_email_page.content.decode()
    assert signup.email in check_email_page.content.decode()
    assert "_auth_user_id" not in client.session
    assert not User.objects.exists()


def test_web_signup_confirmation_link_cannot_be_reused(client, signup_request_factory):
    signup = signup_request_factory(email="verify@example.com", password=PASSWORD)
    url = f"/signup/confirm/{signup.token}/"

    first = client.get(url)
    assert first.status_code == 200
    assert first.context["verified"] is True
    user = User.objects.get(email=signup.email)
    verified_at = user.email_verified_at
    assert verified_at is not None
    assert user.last_login is None
    assert not SignupRequest.objects.exists()
    assert "_auth_user_id" not in client.session

    second = client.get(url)
    assert second.status_code == 200
    assert second.context["verified"] is False
    assert "Ссылка недействительна или истекла" in second.content.decode()
    user.refresh_from_db()
    assert user.email_verified_at == verified_at
    assert User.objects.count() == 1


def test_web_resend_uses_signup_email_from_session(client, mailoutbox):
    data = {"email": "pending@example.com", "password": PASSWORD, "password_confirm": PASSWORD}
    assert client.post("/signup/", data).status_code == 302
    signup = SignupRequest.objects.get()
    cache.clear()
    response = client.post(reverse("web:resend-verification"), {"email": "attacker@example.com"})
    assert response.status_code == 302
    assert response.url == "/signup/check-email/"
    assert len(mailoutbox) == 2
    assert mailoutbox[1].to == [signup.email]
    assert f"/signup/confirm/{signup.token}/" in mailoutbox[1].body
    assert not User.objects.exists()


def test_web_resend_without_signup_session_has_same_redirect(client, mailoutbox):
    response = client.post(reverse("web:resend-verification"))
    assert response.status_code == 302
    assert response.url == "/signup/check-email/"
    assert not mailoutbox


def test_web_repeat_signup_during_cooldown_keeps_request_and_email(client, mailoutbox):
    data = {"email": "pending@example.com", "password": PASSWORD, "password_confirm": PASSWORD}
    first_response = client.post("/signup/", data)
    first = SignupRequest.objects.get()
    second_response = client.post("/signup/", {**data, "email": data["email"].upper(), "first_name": "Новое имя"})
    second = SignupRequest.objects.get()

    assert first_response.status_code == second_response.status_code == 302
    assert first_response.url == second_response.url == "/signup/check-email/"
    assert second.pk == first.pk
    assert second.token == first.token
    assert second.first_name == first.first_name
    assert len(mailoutbox) == 1
    assert not User.objects.exists()
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("field,value", [("password", "12345678"), ("password_confirm", "Mismatch"), ("phone", "abc")])
def test_signup_validation(client, field, value):
    data = {"email": "new@example.com", "password": PASSWORD, "password_confirm": PASSWORD, field: value}
    response = client.post("/signup/", data)
    assert response.status_code == 200
    assert field in response.context["form"].errors
    assert not User.objects.exists()
    assert not SignupRequest.objects.exists()


def test_signup_duplicate_email(client, user):
    response = client.post(
        "/signup/", {"email": user.email.upper(), "password": PASSWORD, "password_confirm": PASSWORD}
    )
    assert response.status_code == 200
    assert "email" in response.context["form"].errors
    assert User.objects.count() == 1
    assert not SignupRequest.objects.exists()


def test_signup_duplicate_phone(client, user):
    response = client.post(
        "/signup/",
        {"email": "new@example.com", "phone": user.phone, "password": PASSWORD, "password_confirm": PASSWORD},
    )
    assert response.status_code == 200
    assert "phone" in response.context["form"].errors
    assert not SignupRequest.objects.exists()
    assert User.objects.count() == 1


def test_logout_post_ends_session(web_client):
    assert web_client.get("/logout/").status_code == 405
    assert "_auth_user_id" in web_client.session
    assert web_client.post("/logout/").status_code == 302
    assert "_auth_user_id" not in web_client.session


def test_photo_and_placeholder(client, user, ad):
    photo_ad = Ad.objects.create(**DATA, author=user, image=image_file())
    response = client.get("/")
    html = response.content.decode()
    assert photo_ad.image.url in html
    assert "Нет фото" in html
    assert photo_ad.image.url in client.get(detail(photo_ad)).content.decode()
    assert "Нет фото" in client.get(detail(ad)).content.decode()


def test_reset_email_and_new_password(client, user, mailoutbox):
    response = client.post("/password-reset/", {"email": user.email})
    assert response.status_code == 302 and response.url == "/password-reset/done/"
    assert len(mailoutbox) == 1
    link = re.search(r"http://testserver(/password-reset/\S+/)", mailoutbox[0].body).group(1)
    form_page = client.get(link, follow=True)
    assert form_page.status_code == 200 and form_page.context["validlink"]
    clean_path = form_page.redirect_chain[-1][0]
    password = "New-Strong-Password-2026!"
    result = client.post(clean_path, {"new_password1": password, "new_password2": password})
    assert result.status_code == 302 and result.url == "/password-reset/complete/"
    user.refresh_from_db()
    assert user.check_password(password) and not user.check_password(PASSWORD)
    assert client.post("/login/", {"username": user.email, "password": password}).status_code == 302
    assert not client.get(link, follow=True).context["validlink"]


def test_api_reset_link_works_on_web(client, user, mailoutbox):
    assert client.post("/api/users/reset_password/", {"email": user.email}).status_code == 204
    link = re.search(r"http://testserver(/password-reset/\S+/)", mailoutbox[0].body).group(1)
    response = client.get(link, follow=True)
    assert response.context["validlink"]


def test_unknown_reset_same_page_no_email(client, mailoutbox):
    response = client.post("/password-reset/", {"email": "unknown@example.com"})
    assert response.url == "/password-reset/done/"
    assert not mailoutbox


@pytest.mark.parametrize("case", ["invalid", "expired", "inactive"])
def test_bad_reset_link(client, user, case):
    uid, token = make_reset_credentials(user)
    if case == "invalid":
        token += "bad"
    if case == "inactive":
        user.is_active = False
        user.save()
    url = reverse("web:password-reset-confirm", args=[uid, token])
    future = default_token_generator._now() + timedelta(days=2 if case == "expired" else 0)
    with patch.object(default_token_generator, "_now", return_value=future):
        response = client.get(url, follow=True)
    assert response.status_code == 200
    assert not response.context["validlink"]


def test_reset_rejects_weak_password(client, user):
    uid, token = make_reset_credentials(user)
    response = client.get(reverse("web:password-reset-confirm", args=[uid, token]), follow=True)
    response = client.post(response.redirect_chain[-1][0], {"new_password1": "12345678", "new_password2": "12345678"})
    assert response.status_code == 200
    assert response.context["form"].errors
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_csrf_enforced(user):
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(user)
    assert browser.post("/ad/new/", DATA).status_code == 403
    browser.get("/ad/new/")
    response = browser.post("/ad/new/", {**DATA, "csrfmiddlewaretoken": browser.cookies["csrftoken"].value})
    assert response.status_code == 302


def test_web_session_does_not_authorize_api(web_client):
    assert web_client.get("/api/users/me/").status_code == 401
    assert web_client.get("/account/").status_code == 200


def test_user_content_escaped(client, user):
    ad = Ad.objects.create(
        title="<script>alert(1)</script>", price=0, description="<img src=x onerror=alert(1)>", author=user
    )
    html = client.get(detail(ad)).content.decode()
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html


def test_error_templates(client, web_client, ad, settings):
    settings.DEBUG = False
    assert client.get("/nonexistent/").status_code == 404
    response = server_error_view(RequestFactory().get("/"))
    assert response.status_code == 500
    assert "Попробуйте" in response.content.decode()


@pytest.mark.parametrize(
    "url", ["/login/", "/signup/", "/password-reset/", "/password-reset/done/", "/password-reset/complete/"]
)
def test_public_account_pages(client, url):
    assert client.get(url).status_code == 200


@pytest.fixture
def account_states(user, other_user):
    ads = [
        Ad.objects.create(**{**DATA, "status": status}, author=user) for status in ("draft", "published", "archived")
    ]
    Ad.objects.create(**DATA, author=other_user)
    return ads


def test_account_all_states(web_client, account_states, user):
    response = web_client.get("/account/")
    assert set(response.context["ads"]) == set(account_states)
    assert response.context["page_obj"].paginator.count == 3
    assert response.context["active_status"] == ""
    assert user.email in response.content.decode()


def test_account_draft(web_client, account_states):
    response = web_client.get("/account/?status=draft")
    assert list(response.context["ads"]) == [account_states[0]]
    assert response.context["active_status"] == "draft"
    assert 'aria-current="page">На рассмотрении' in response.content.decode()


def test_account_published(web_client, account_states):
    assert list(web_client.get("/account/?status=published").context["ads"]) == [account_states[1]]


def test_account_archived(web_client, account_states):
    assert list(web_client.get("/account/?status=archived").context["ads"]) == [account_states[2]]


def test_account_invalid_status(web_client, account_states):
    response = web_client.get("/account/?status=wrong")
    assert response.status_code == 200
    assert set(response.context["ads"]) == set(account_states)
    assert response.context["active_status"] == ""


def test_account_status_with_search(web_client, account_states, user):
    Ad.objects.create(**{**DATA, "title": "Другой", "status": "draft"}, author=user)
    response = web_client.get("/account/", {"status": "draft", "search": DATA["title"]})
    assert list(response.context["ads"]) == [account_states[0]]
    assert response.context["page_obj"].paginator.count == 1
    assert 'class="header-search" action="/account/"' in response.content.decode()
    assert 'name="status" value="draft"' in response.content.decode()


def test_account_filtered_pagination(web_client, account_states, user):
    for _ in range(5):
        Ad.objects.create(**{**DATA, "status": "draft"}, author=user)
    response = web_client.get("/account/", {"status": "draft", "search": "стол", "page": 2})
    assert response.context["page_obj"].paginator.count == 6
    assert len(response.context["ads"]) == 2
    html = response.content.decode()
    assert "status=draft" in html and "search=" in html
    assert "page=2" not in html.split('aria-label="Состояние объявлений"')[1].split("</nav>")[0]


def test_login_unknown_and_wrong_password_same_error(client, user):
    errors = []
    for email in (user.email, "missing@example.com"):
        response = client.post("/login/", {"username": email, "password": "wrong"})
        assert response.status_code == 200
        errors.append(response.context["form"].errors.as_json())
        assert response.content.decode().count("Неверная почта или пароль.") == 1
    assert errors[0] == errors[1]


def test_login_error_does_not_reveal_account(client, user):
    user.is_active = False
    user.save()
    response = client.post("/login/", {"username": user.email, "password": PASSWORD})
    assert list(response.context["form"].non_field_errors()) == ["Неверная почта или пароль."]
    assert "неактив" not in response.content.decode().lower()


@pytest.mark.parametrize("data", [{}, {"username": "broken", "password": "wrong"}, {"username": "u@example.com"}])
def test_login_validation_single_error(client, data):
    form = client.post("/login/", data).context["form"]
    assert list(form.errors) == ["__all__"]
    assert list(form.non_field_errors()) == ["Неверная почта или пароль."]


def test_queue_row(client, admin, ad):
    ad.status = "draft"
    ad.submitted_at = timezone.now()
    ad.save()
    client.force_login(admin)
    response = client.get("/account/review/")
    assert response.status_code == 200
    row = re.search(r'<article class="review-row">(.*?)</article>', response.content.decode(), re.S).group(1)
    assert ad.title in row and ad.author.first_name in row
    assert f"Отправлено {timezone.localtime(ad.submitted_at):%d.%m.%Y}" in row
    assert f'href="/ad/{ad.pk}/"' in row
    assert "На рассмотрении" not in row and "ad-status" not in row
    assert "На рассмотрении" in response.content.decode()
