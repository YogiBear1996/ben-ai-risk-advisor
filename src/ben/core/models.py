"""Channel-agnostic data types shared by the core and the adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Channel(StrEnum):
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    CLI = "cli"


@dataclass(frozen=True)
class IncomingMessage:
    """A message normalised from any channel."""

    channel: Channel
    chat_id: str
    user_id: str
    text: str
    display_name: str | None = None
    message_id: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    @property
    def is_command(self) -> bool:
        return self.text.strip().startswith("/")


@dataclass(frozen=True)
class SourceRef:
    """A retrievable source a claim can be grounded in."""

    doc_title: str
    section: str | None = None
    page: int | None = None
    source_path: str | None = None
    chunk_id: str | None = None
    control_id: str | None = None

    def label(self) -> str:
        text = self.doc_title
        detail = self.control_id or self.section
        if detail:
            text += f" — {detail}"
        if self.page:
            text += f", p.{self.page}"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRef:
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__})


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, other: Any) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + (getattr(other, name, 0) or 0))


@dataclass
class AgentResult:
    text: str
    sources: list[SourceRef]
    usage: Usage
    model: str
    latency_ms: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    unverified_ids: list[str] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass
class OutgoingReply:
    chat_id: str
    text: str
