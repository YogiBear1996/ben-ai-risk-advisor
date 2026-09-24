from __future__ import annotations

from fastapi.testclient import TestClient

from ben.channels.telegram import TelegramAdapter
from ben.web.app import create_app
from tests.fakes import text_response
from tests.helpers import FakeAdapter, make_service
from tests.test_telegram import UPDATE, FakeBot


def make_client(settings, responses, **adapters):
    service, fake = make_service(settings, responses)
    app = create_app(settings, service=service, adapters=adapters)
    return TestClient(app), fake


def test_health(settings):
    client, _ = make_client(settings, [])
    with client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["model"] == settings.ben_model


def test_telegram_webhook_rejects_bad_secret(settings):
    tg = FakeAdapter(TelegramAdapter(FakeBot()))
    client, _ = make_client(settings, [], telegram=tg)
    with client:
        assert client.post("/webhooks/telegram", json=UPDATE).status_code == 401
        r = client.post(
            "/webhooks/telegram", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "no"}
        )
        assert r.status_code == 401
    assert tg.sent == []


def test_telegram_webhook_answers_with_valid_secret(settings):
    tg = FakeAdapter(TelegramAdapter(FakeBot()))
    client, _ = make_client(settings, [text_response("Hello from Ben")], telegram=tg)
    with client:
        r = client.post(
            "/webhooks/telegram",
            json=UPDATE,
            headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"},
        )
    assert r.status_code == 200
    assert tg.sent == [("111", "Hello from Ben")]


def test_telegram_webhook_404_when_not_configured(settings):
    settings.telegram_bot_token = None
    client, _ = make_client(settings, [])
    with client:
        assert client.post("/webhooks/telegram", json=UPDATE).status_code == 404
