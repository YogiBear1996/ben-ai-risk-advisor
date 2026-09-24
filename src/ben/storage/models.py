"""SQL tables. Portable across SQLite and Postgres (no dialect-specific types)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    channel: str = Field(index=True)
    external_id: str = Field(index=True)  # Telegram user ID / WhatsApp number
    display_name: str | None = None
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    channel: str = Field(index=True)
    chat_id: str = Field(index=True)
    active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    # Sources retrieved/cited on the last answer (for /sources)
    last_sources: list[dict] | None = Field(default=None, sa_column=Column(JSON))


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    role: str  # user | assistant
    content: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AuditLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=utcnow, index=True)
    channel: str = Field(index=True)
    user_id: str = Field(index=True)
    chat_id: str | None = None
    status: str = Field(default="ok", index=True)  # ok | denied | rate_limited | command | error
    question: str | None = Field(default=None, sa_column=Column(Text))
    answer: str | None = Field(default=None, sa_column=Column(Text))
    sources: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    tool_calls: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    unverified_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    redacted: bool = False


class Control(SQLModel, table=True):
    control_id: str = Field(primary_key=True)
    risk_category: str | None = Field(default=None, index=True)
    ai_type: str | None = Field(default=None, index=True)
    risk_description: str | None = Field(default=None, sa_column=Column(Text))
    control_description: str | None = Field(default=None, sa_column=Column(Text))
    owner: str | None = None
    framework_mapping: str | None = Field(default=None, sa_column=Column(Text))
    extra: dict | None = Field(default=None, sa_column=Column(JSON))
    source_path: str | None = None


class IngestedFile(SQLModel, table=True):
    path: str = Field(primary_key=True)  # relative to the knowledge dir
    sha256: str
    embedder: str = ""  # embedding model used; a change forces re-ingestion
    title: str
    doc_type: str  # document | control
    chunks: int = 0
    ingested_at: datetime = Field(default_factory=utcnow)
