"""Shared builders for service/adapter tests."""

from __future__ import annotations

from ben.core.models import Channel, IncomingMessage
from ben.core.service import BenService
from ben.runtime import build_agent
from tests.fakes import FakeAnthropic
from tests.test_agent import StubLibrary


def make_service(settings, responses, library=None) -> tuple[BenService, FakeAnthropic]:
    client = FakeAnthropic(responses)
    library = library or StubLibrary()
    agent = build_agent(settings, library=library, client=client)
    return BenService(settings, agent, library), client


def tg_msg(text: str, user: str = "111", chat: str = "111", mid: str | None = None):
    return IncomingMessage(Channel.TELEGRAM, chat, user, text, "Test User", mid)


class FakeAdapter:
    """Records what would have been sent."""

    def __init__(self, real) -> None:
        self.real = real
        self.sent: list[tuple[str, str]] = []

    def parse_incoming(self, payload):
        return self.real.parse_incoming(payload)

    async def send_typing(self, chat_id):
        pass

    async def send_reply(self, chat_id, text):
        self.sent.append((chat_id, text))
