"""Проверка локального контейнера: register, create --mail-log, затем verify."""

import argparse
import base64
import json
import os
import re
import uuid
from email import policy
from email.parser import Parser
from email.utils import getaddresses
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


def request(base, path, *, method="GET", data=None, token=None, expected=200, content_type="application/json"):
    display_path = "/signup/confirm/<token>/" if path.startswith("/signup/confirm/") else path.split("?")[0]
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None and content_type == "application/json" else data
    req = Request(base + path, data=body, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        assert response.status == expected, f"{method} {display_path}: expected {expected}, got {response.status}"
        raw = response.read()
        print(f"{method} {display_path} → {response.status}")
        return json.loads(raw) if raw and "application/json" in response.headers.get("Content-Type", "") else raw


def login(base, state):
    return request(
        base, "/api/users/token/", method="POST", data={"email": state["email"], "password": state["password"]}
    )["access"]


def save_state(state_path, state):
    descriptor = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as state_file:
        os.fchmod(state_file.fileno(), 0o600)
        json.dump(state, state_file)


def register(base, state_path):
    state = {"email": f"smoke-{uuid.uuid4().hex}@example.com", "password": uuid.uuid4().hex + "-Str0ng!"}
    request(
        base,
        "/api/users/register/",
        method="POST",
        data={**state, "password_confirm": state["password"]},
        expected=201,
    )
    request(base, "/api/users/token/", method="POST", data=state, expected=401)
    save_state(state_path, state)
    print("Signup request created; waiting for the console email")


def confirmation_path(base, email, mail_log):
    # Django's console backend ends each MIME message with 79 hyphens.
    for chunk in reversed(re.split(r"(?m)^-{79}\r?$", mail_log.read_text())):
        start = re.search(r"(?m)^(?:Content-Type|MIME-Version|From|To|Subject|Date|Message-ID):", chunk)
        if start is None:
            continue
        message = Parser(policy=policy.default).parsestr(chunk[start.start() :])
        recipients = {address.lower() for _, address in getaddresses(message.get_all("To", []))}
        if email.lower() not in recipients:
            continue
        for part in message.walk():
            if part.get_content_type() != "text/plain":
                continue
            match = re.search(r"https?://[^\s<>\"']+/signup/confirm/[A-Za-z0-9_-]+/", part.get_content())
            if match:
                link, origin = urlsplit(match.group()), urlsplit(base)
                assert (link.scheme, link.netloc) == (
                    origin.scheme,
                    origin.netloc,
                ), "The confirmation email's FRONTEND_URL must match the local smoke URL"
                return link.path
    raise AssertionError("No signup confirmation email found for this smoke account")


def create(base, state_path, mail_log):
    state = json.loads(state_path.read_text())
    path = confirmation_path(base, state["email"], mail_log)
    first = request(base, path).decode()
    assert "Ссылка недействительна или истекла" not in first
    assert "Ссылка недействительна или истекла" in request(base, path).decode()
    token = login(base, state)
    boundary = "image-upload-boundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="pixel.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + IMAGE
        + f"\r\n--{boundary}--\r\n".encode()
    )
    profile = request(
        base,
        "/api/users/me/",
        method="PATCH",
        data=body,
        token=token,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    state["user_id"] = profile["id"]
    state["image"] = urlsplit(profile["image"]).path
    ad = request(
        base,
        "/api/ads/",
        method="POST",
        token=token,
        data={"title": "Smoke bicycle", "price": 1234, "description": "Persistence check"},
        expected=201,
    )
    state["ad_id"] = ad["id"]
    review = request(
        base, f"/api/ads/{ad['id']}/reviews/", method="POST", token=token, data={"text": "Smoke review"}, expected=201
    )
    state["review_id"] = review["id"]
    request(base, "/users/reset_password/", method="POST", data={"email": "missing@example.com"}, expected=204)
    save_state(state_path, state)
    verify(base, state_path)


def verify(base, state_path):
    state = json.loads(state_path.read_text())
    request(base, "/health/")
    assert "маркет.код" in request(base, "/").decode()
    request(base, "/static/css/site.css")
    request(base, "/static/js/navigation.js")
    request(base, "/api/docs/")
    request(base, "/api/schema/?format=json")
    request(base, "/api/users/me/", expected=401)
    token = login(base, state)
    profile = request(base, "/api/users/me/", token=token)
    assert profile["id"] == state["user_id"]
    assert urlsplit(profile["image"]).path == state["image"]
    assert request(base, state["image"]) == IMAGE
    ad = request(base, f"/api/ads/{state['ad_id']}/", token=token)
    assert ad["author"]["id"] == state["user_id"]
    assert ad["price"] == 1234
    request(base, f"/api/ads/{state['ad_id']}/", expected=401)
    reviews = request(base, f"/api/ads/{state['ad_id']}/reviews/", token=token)
    assert state["review_id"] in [item["id"] for item in reviews["results"]]
    result = request(base, "/ads/?search=Smoke&price_min=1000&ordering=price")
    assert result["count"] >= 1
    print("Data and uploaded image verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "create", "verify"))
    parser.add_argument("base", help="URL of the local container")
    parser.add_argument("state_path", type=Path)
    parser.add_argument(
        "--mail-log", type=Path, help="Console mail from docker compose logs --no-log-prefix --no-color web"
    )
    args = parser.parse_args()
    if (args.mode == "create") != (args.mail_log is not None):
        parser.error("--mail-log is required only for create")
    if args.mode == "create":
        create(args.base.rstrip("/"), args.state_path, args.mail_log)
    else:
        {"register": register, "verify": verify}[args.mode](args.base.rstrip("/"), args.state_path)
