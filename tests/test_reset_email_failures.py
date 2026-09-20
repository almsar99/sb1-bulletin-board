"""Отказ отправки письма не раскрывает учётную запись и данные восстановления."""

import logging
from smtplib import SMTPException
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.django_db


def fail_delivery(subject, body, sender, recipients, *, html_message):
    raise SMTPException(f"{recipients}: {body} {html_message}")


@pytest.mark.parametrize("url", ["/api/users/reset_password/", "/users/reset_password/"])
def test_api_reset_mail_failure_has_same_response_and_safe_log(api_client, user, caplog, url):
    with caplog.at_level(logging.ERROR, logger="users.email"):
        with patch("users.email.send_mail", side_effect=fail_delivery) as send_mail:
            unknown = api_client.post(url, {"email": "missing@example.com"})
            assert not send_mail.called
            assert not caplog.records
            known = api_client.post(url, {"email": user.email})

    assert known.status_code == unknown.status_code == 204
    assert known.content == unknown.content == b""
    send_mail.assert_called_once()
    records = [record for record in caplog.records if record.name == "users.email"]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == f"Не удалось отправить письмо восстановления пароля пользователю {user.pk}"
    assert record.exc_info is None
    assert user.email not in caplog.text
    assert "/password-reset/" not in caplog.text
    assert send_mail.call_args.args[1] not in caplog.text
    assert send_mail.call_args.kwargs["html_message"] not in caplog.text


def test_site_reset_mail_failure_shows_normal_confirmation(client, user, caplog):
    with caplog.at_level(logging.ERROR, logger="users.email"):
        with patch("users.email.send_mail", side_effect=fail_delivery) as send_mail:
            unknown = client.post("/password-reset/", {"email": "missing@example.com"}, follow=True)
            known = client.post("/password-reset/", {"email": user.email}, follow=True)

    assert unknown.redirect_chain == known.redirect_chain == [("/password-reset/done/", 302)]
    assert unknown.status_code == known.status_code == 200
    assert "Если аккаунт с таким адресом существует, мы отправили письмо со ссылкой." in known.content.decode()
    send_mail.assert_called_once()
    assert user.email not in caplog.text
    assert "/password-reset/" not in caplog.text
