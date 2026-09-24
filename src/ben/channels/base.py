"""The ChannelAdapter interface. The core never knows which channel it is on."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ben.channels.formatting import split_message
from ben.core.models import Channel, IncomingMessage


class ChannelAdapter(ABC):
    channel: Channel
    # Split the (unformatted) reply below the platform limit, leaving headroom for markup.
    chunk_chars: int = 3500

    @abstractmethod
    def parse_incoming(self, payload: dict[str, Any]) -> list[IncomingMessage]:
        """Normalise a platform webhook payload into zero or more messages."""

    @abstractmethod
    def format(self, text: str) -> str:
        """Convert Ben's Markdown into the platform's formatting."""

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None:
        """Send one already-formatted message."""

    async def send_typing(self, chat_id: str) -> None:  # noqa: B027 - optional hook
        """Show a typing indicator, where the platform supports it."""

    async def send_reply(self, chat_id: str, text: str) -> int:
        """Format and send a reply, split into as many messages as needed."""
        parts = split_message(text, self.chunk_chars)
        total = len(parts)
        for i, part in enumerate(parts, start=1):
            suffix = f"\n\n({i}/{total})" if total > 1 else ""
            await self.send_text(chat_id, self.format(part) + suffix)
        return total
