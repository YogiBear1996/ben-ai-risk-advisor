"""BenService: the channel-agnostic pipeline from an incoming message to a reply.

access check -> rate limit -> command | agent -> memory -> audit
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from ben.config import Settings
from ben.core.agent import BenAgent
from ben.core.commands import CommandHandler
from ben.core.models import IncomingMessage
from ben.knowledge.types import Library
from ben.storage.repo import ConversationRepo

log = logging.getLogger(__name__)

UNSUPPORTED = "I can only read text messages for now - please type your question."
MAX_INPUT_CHARS = 8000


class _Seen:
    """Bounded set of recently processed message IDs (webhooks can be redelivered)."""

    def __init__(self, size: int = 2048) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def check_and_add(self, key: str) -> bool:
        if key in self._ids:
            return True
        self._ids[key] = None
        if len(self._ids) > self._size:
            self._ids.popitem(last=False)
        return False


class BenService:
    def __init__(
        self,
        settings: Settings,
        agent: BenAgent,
        library: Library,
        repo: ConversationRepo | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.library = library
        self.repo = repo or ConversationRepo(settings)
        self.commands = CommandHandler(self.repo, library)
        self._seen = _Seen()
        self._locks: dict[str, asyncio.Lock] = {}

    async def handle(self, msg: IncomingMessage) -> str | None:
        """Process one message. Returns the reply text, or None if nothing should be sent."""
        if msg.message_id and self._seen.check_and_add(f"{msg.channel}:{msg.message_id}"):
            log.info("Duplicate delivery of %s ignored", msg.message_id)
            return None

        self.repo.touch_user(msg.channel.value, msg.user_id, msg.display_name)

        if msg.is_command:
            return self.commands.handle(msg)
        if not msg.text:
            return UNSUPPORTED

        # One answer at a time per chat keeps memory ordered.
        lock = self._locks.setdefault(f"{msg.channel}:{msg.chat_id}", asyncio.Lock())
        async with lock:
            return await self._answer(msg)

    async def _answer(self, msg: IncomingMessage) -> str:
        text = msg.text[:MAX_INPUT_CHARS]
        history = self.repo.history(msg.channel.value, msg.chat_id, self.settings.ben_history_turns)
        result = await asyncio.to_thread(self.agent.respond, history, text)
        if result.stop_reason != "error":
            self.repo.append_exchange(
                msg.channel.value, msg.chat_id, text, result.text, result.sources
            )
        return result.text
