"""WhatsApp Cloud API (Meta Graph API) adapter."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from ben.channels.base import ChannelAdapter
from ben.channels.formatting import to_whatsapp
from ben.core.models import Channel, IncomingMessage

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header (HMAC-SHA256 of the raw body)."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


class WhatsAppAdapter(ChannelAdapter):
    channel = Channel.WHATSAPP
    chunk_chars = 3500  # WhatsApp text body limit is 4096 characters

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v21.0",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = access_token
        self.phone_number_id = phone_number_id
        self.url = f"{GRAPH_URL}/{api_version}/{phone_number_id}/messages"
        self.client = client or httpx.AsyncClient(timeout=20.0)

    def parse_incoming(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        messages: list[IncomingMessage] = []
        if payload.get("object") != "whatsapp_business_account":
            return messages
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                metadata = value.get("metadata") or {}
                if metadata.get("phone_number_id") not in (None, self.phone_number_id):
                    continue  # another number on the same app
                names = {
                    c.get("wa_id"): (c.get("profile") or {}).get("name")
                    for c in value.get("contacts") or []
                }
                # value["statuses"] (delivery/read receipts) are ignored.
                for m in value.get("messages") or []:
                    sender = m.get("from")
                    if not sender:
                        continue
                    text = (m.get("text") or {}).get("body", "") if m.get("type") == "text" else ""
                    messages.append(
                        IncomingMessage(
                            channel=Channel.WHATSAPP,
                            chat_id=sender,
                            user_id=sender,
                            text=text.strip(),
                            display_name=names.get(sender),
                            message_id=m.get("id"),
                            raw=m,
                        )
                    )
        return messages

    def format(self, text: str) -> str:
        return to_whatsapp(text)

    async def send_text(self, chat_id: str, text: str) -> None:
        response = await self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self._token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": chat_id,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            },
        )
        if response.is_error:
            log.error("WhatsApp send failed: HTTP %s %s", response.status_code, response.text[:500])
            response.raise_for_status()

    async def aclose(self) -> None:
        await self.client.aclose()
