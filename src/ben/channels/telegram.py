"""Telegram adapter (python-telegram-bot's Bot client for sending)."""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from telegram import Bot
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest

from ben.channels.base import ChannelAdapter
from ben.channels.formatting import to_telegram_html
from ben.core.models import Channel, IncomingMessage

log = logging.getLogger(__name__)


class TelegramAdapter(ChannelAdapter):
    channel = Channel.TELEGRAM
    chunk_chars = 3500  # Telegram limit is 4096 characters after entity parsing

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    def parse_incoming(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        message = payload.get("message")
        if not isinstance(message, dict):
            return []  # edits, channel posts, callbacks etc. are ignored
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        if sender.get("is_bot") or "id" not in sender or "id" not in chat:
            return []
        name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")]))
        return [
            IncomingMessage(
                channel=Channel.TELEGRAM,
                chat_id=str(chat["id"]),
                user_id=str(sender["id"]),
                text=(message.get("text") or "").strip(),
                display_name=name or sender.get("username"),
                message_id=f"{chat['id']}:{message.get('message_id')}",
                raw=payload,
            )
        ]

    def format(self, text: str) -> str:
        return to_telegram_html(text)

    async def send_text(self, chat_id: str, text: str) -> None:
        try:
            await self.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        except BadRequest:
            # Malformed markup should never lose the answer - resend as plain text.
            log.warning("Telegram rejected HTML; resending as plain text")
            await self.bot.send_message(chat_id=chat_id, text=_strip_tags(text))

    async def send_typing(self, chat_id: str) -> None:
        try:
            await self.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:  # cosmetic only
            log.debug("send_chat_action failed", exc_info=True)


def _strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"</?[a-z]+[^>]*>", "", text))
