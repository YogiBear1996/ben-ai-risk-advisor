"""BenService: the channel-agnostic pipeline from an incoming message to a reply.

dedupe -> allowlist -> rate limit -> command | (redact?) agent -> memory -> audit
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict

from ben.config import Settings
from ben.core.agent import BenAgent
from ben.core.commands import CommandHandler
from ben.core.models import AgentResult, IncomingMessage
from ben.knowledge.types import Library
from ben.security.allowlist import is_allowed
from ben.security.ratelimit import RateLimiter
from ben.security.redaction import redact
from ben.storage.models import AuditLog
from ben.storage.repo import AuditRepo, ConversationRepo

log = logging.getLogger(__name__)

UNSUPPORTED = "I can only read text messages for now - please type your question."
DENIED = (
    "Sorry - I'm only available to approved members of the AI governance programme. "
    "To request access, contact the AI governance team and quote your ID: {user_id}"
)
RATE_LIMITED = "You're sending messages quickly - please wait a minute and try again."
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
        audit: AuditRepo | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.library = library
        self.repo = repo or ConversationRepo(settings)
        self.audit = audit or AuditRepo(settings)
        self.commands = CommandHandler(self.repo, library)
        self.limiter = RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_burst)
        self._seen = _Seen()
        self._locks: dict[str, asyncio.Lock] = {}

    def _for_log(self, text: str | None) -> tuple[str | None, bool]:
        if not self.settings.redact_before_logging or not text:
            return text, False
        result = redact(text)
        return result.text, result.redacted

    def _audit(
        self, msg: IncomingMessage, status: str, answer: str | None = None, result=None
    ) -> None:
        question, q_red = self._for_log(msg.text)
        answer, a_red = self._for_log(answer)
        entry = AuditLog(
            channel=msg.channel.value,
            user_id=msg.user_id,
            chat_id=msg.chat_id,
            status=status,
            question=question,
            answer=answer,
            redacted=q_red or a_red,
        )
        if isinstance(result, AgentResult):
            entry.sources = [s.to_dict() for s in result.sources]
            entry.tool_calls = result.tool_calls
            entry.unverified_ids = result.unverified_ids or None
            entry.model = result.model
            entry.input_tokens = result.usage.input_tokens
            entry.output_tokens = result.usage.output_tokens
            entry.cache_read_tokens = result.usage.cache_read_input_tokens
            entry.cache_write_tokens = result.usage.cache_creation_input_tokens
            entry.latency_ms = result.latency_ms
        try:
            self.audit.record(entry)
        except Exception:  # auditing must never break answering, but must be visible
            log.exception("Failed to write audit log entry")

    async def handle(self, msg: IncomingMessage) -> str | None:
        """Process one message. Returns the reply text, or None if nothing should be sent."""
        if msg.message_id and self._seen.check_and_add(f"{msg.channel}:{msg.message_id}"):
            log.info("Duplicate delivery ignored")
            return None

        if not is_allowed(self.settings, msg.channel, msg.user_id):
            log.warning("Denied %s user %s", msg.channel.value, msg.user_id)
            self._audit(msg, "denied")
            return DENIED.format(user_id=msg.user_id)

        if not self.limiter.allow(f"{msg.channel}:{msg.user_id}"):
            self._audit(msg, "rate_limited")
            return RATE_LIMITED

        self.repo.touch_user(msg.channel.value, msg.user_id, msg.display_name)

        if msg.is_command:
            reply = self.commands.handle(msg)
            self._audit(msg, "command")
            return reply
        if not msg.text:
            self._audit(msg, "unsupported")
            return UNSUPPORTED

        # One answer at a time per chat keeps memory ordered.
        lock = self._locks.setdefault(f"{msg.channel}:{msg.chat_id}", asyncio.Lock())
        async with lock:
            return await self._answer(msg)

    async def _answer(self, msg: IncomingMessage) -> str:
        text = msg.text[:MAX_INPUT_CHARS]
        if self.settings.redact_before_model:
            text = redact(text).text
        history = self.repo.history(msg.channel.value, msg.chat_id, self.settings.ben_history_turns)
        result = await asyncio.to_thread(self.agent.respond, history, text)
        status = "error" if result.stop_reason == "error" else "ok"
        if status == "ok":
            # Memory is stored like logs: redacted when REDACT_BEFORE_LOGGING is on.
            stored_q, _ = self._for_log(text)
            stored_a, _ = self._for_log(result.text)
            self.repo.append_exchange(
                msg.channel.value, msg.chat_id, stored_q, stored_a, result.sources
            )
        if result.unverified_ids:
            log.warning("Answer mentioned unknown control IDs: %s", result.unverified_ids)
        self._audit(msg, status, result.text, result)
        return result.text
