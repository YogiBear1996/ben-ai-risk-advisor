"""Slash commands shared by every channel."""

from __future__ import annotations

from collections.abc import Callable

from ben.core.models import IncomingMessage
from ben.knowledge.types import Library
from ben.storage.repo import ConversationRepo

WELCOME = (
    "Hi, I'm **Ben**, your AI governance assistant.\n\n"
    "Ask me about AI risk, controls, policy and regulation - for example:\n"
    "- Which controls apply to a GenAI customer-service chatbot?\n"
    "- What does ISO/IEC 42001 require for an AI impact assessment?\n"
    "- Is a CV-screening model high-risk under the EU AI Act?\n\n"
    "I answer from our document library and cite my sources. I'm not a lawyer - "
    "Legal/Compliance should confirm regulatory interpretations. Type /help for commands."
)

HELP = (
    "**Commands**\n"
    "- /start - introduction\n"
    "- /help - this message\n"
    "- /reset - start a new conversation (I forget the previous one)\n"
    "- /sources - show the sources behind my last answer\n"
    "- /frameworks - list the documents and frameworks I know\n\n"
    "Tip: don't share passenger or staff personal data - describe the use case instead."
)


class CommandHandler:
    def __init__(self, repo: ConversationRepo, library: Library) -> None:
        self.repo = repo
        self.library = library
        self._handlers: dict[str, Callable[[IncomingMessage], str]] = {
            "start": lambda m: WELCOME,
            "help": lambda m: HELP,
            "reset": self._reset,
            "sources": self._sources,
            "frameworks": self._frameworks,
        }

    @staticmethod
    def parse(text: str) -> str:
        """'/Sources@BenBot extra' -> 'sources'."""
        return text.strip().split()[0].lstrip("/").split("@")[0].lower()

    def handle(self, msg: IncomingMessage) -> str:
        handler = self._handlers.get(self.parse(msg.text))
        return handler(msg) if handler else "Unknown command. " + HELP

    def _reset(self, msg: IncomingMessage) -> str:
        self.repo.reset(msg.channel.value, msg.chat_id)
        return "Done - I've started a fresh conversation."

    def _sources(self, msg: IncomingMessage) -> str:
        sources = self.repo.last_sources(msg.channel.value, msg.chat_id)
        if not sources:
            return "I didn't use any library sources for my last answer."
        lines = [f"{i}. {s.label()}" for i, s in enumerate(sources, start=1)]
        return "**Sources retrieved for my last answer**\n" + "\n".join(lines)

    def _frameworks(self, msg: IncomingMessage) -> str:
        items = self.library.list_frameworks()
        if not items:
            return "My library is empty right now - ask an admin to run `ben ingest`."
        lines = [f"- {f.title}" for f in items]
        return "**Documents in my library**\n" + "\n".join(lines)
