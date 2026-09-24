from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from ben.channels.whatsapp import WhatsAppAdapter, verify_signature
from ben.core.models import Channel
from ben.web.app import create_app
from tests.fakes import text_response
from tests.helpers import FakeAdapter, make_service

SEND_URL = "https://graph.facebook.com/v21.0/999/messages"


def wa_payload(text: str | None = "What is Art. 50?", sender: str = "971500000001") -> dict:
    message = {"from": sender, "id": "wamid.1", "timestamp": "1700000000", "type": "text"}
    if text is None:
        message.update(type="image", image={"id": "img"})
    else:
        message["text"] = {"body": text}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "999"},
                            "contacts": [{"wa_id": sender, "profile": {"name": "Sam"}}],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def sign(body: bytes, secret: str = "wa-app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def adapter():
    return WhatsAppAdapter("wa-token", "999", "v21.0", client=httpx.AsyncClient())


def test_parse_text_message(adapter):
    (msg,) = adapter.parse_incoming(wa_payload())
    assert msg.channel is Channel.WHATSAPP
    assert (msg.chat_id, msg.user_id, msg.text) == (
        "971500000001",
        "971500000001",
        "What is Art. 50?",
    )
    assert msg.display_name == "Sam"
    assert msg.message_id == "wamid.1"


def test_parse_ignores_statuses_other_numbers_and_flags_media(adapter):
    status_only = wa_payload()
    status_only["entry"][0]["changes"][0]["value"].pop("messages")
    status_only["entry"][0]["changes"][0]["value"]["statuses"] = [{"status": "read"}]
    assert adapter.parse_incoming(status_only) == []

    other = wa_payload()
    other["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "123"
    assert adapter.parse_incoming(other) == []

    assert adapter.parse_incoming({"object": "page"}) == []
    (media,) = adapter.parse_incoming(wa_payload(text=None))
    assert media.text == ""


def test_signature_verification():
    body = b'{"a": 1}'
    assert verify_signature("wa-app-secret", body, sign(body))
    assert not verify_signature("wa-app-secret", body + b" ", sign(body))
    assert not verify_signature("wa-app-secret", body, "sha1=abc")
    assert not verify_signature("wa-app-secret", body, None)
    assert not verify_signature("", body, sign(body, ""))


@respx.mock
async def test_send_reply_posts_to_graph_api_in_chunks(adapter):
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json={"messages": []}))
    text = "\n\n".join(f"**Point {i}** " + "detail " * 90 for i in range(10))
    count = await adapter.send_reply("971500000001", text)
    assert count == route.call_count > 1
    first = route.calls[0].request
    assert first.headers["Authorization"] == "Bearer wa-token"
    body = json.loads(first.content)
    assert body["to"] == "971500000001" and body["type"] == "text"
    assert body["text"]["body"].startswith("*Point 0*")
    assert all(len(json.loads(c.request.content)["text"]["body"]) <= 4096 for c in route.calls)


@respx.mock
async def test_send_error_raises(adapter):
    respx.post(SEND_URL).mock(return_value=httpx.Response(401, json={"error": "bad token"}))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.send_text("971500000001", "hi")


# --- webhook ------------------------------------------------------------------


def make_client(settings, responses=()):
    service, _ = make_service(settings, list(responses))
    wa = FakeAdapter(WhatsAppAdapter("wa-token", "999"))
    return TestClient(create_app(settings, service=service, adapters={"whatsapp": wa})), wa


def test_verify_token_handshake(settings):
    client, _ = make_client(settings)
    ok = {"hub.mode": "subscribe", "hub.verify_token": "wa-verify", "hub.challenge": "12345"}
    with client:
        r = client.get("/webhooks/whatsapp", params=ok)
        assert r.status_code == 200 and r.text == "12345"
        bad = {**ok, "hub.verify_token": "nope"}
        assert client.get("/webhooks/whatsapp", params=bad).status_code == 403


def test_webhook_rejects_unsigned_or_tampered(settings):
    client, wa = make_client(settings)
    body = json.dumps(wa_payload()).encode()
    with client:
        assert client.post("/webhooks/whatsapp", content=body).status_code == 401
        r = client.post(
            "/webhooks/whatsapp",
            content=body.replace(b"Art. 50", b"Art. 99"),
            headers={"X-Hub-Signature-256": sign(body)},
        )
        assert r.status_code == 401
    assert wa.sent == []


def test_webhook_answers_signed_message(settings):
    client, wa = make_client(settings, [text_response("Chatbots must disclose AI use.")])
    body = json.dumps(wa_payload()).encode()
    with client:
        r = client.post(
            "/webhooks/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": sign(body), "Content-Type": "application/json"},
        )
    assert r.status_code == 200
    assert wa.sent == [("971500000001", "Chatbots must disclose AI use.")]


def test_webhook_refuses_non_allowlisted_number(settings):
    client, wa = make_client(settings)
    body = json.dumps(wa_payload(sender="971509999999")).encode()
    with client:
        client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body)})
    ((chat, reply),) = wa.sent
    assert chat == "971509999999" and "only available" in reply
