"""Persistence helpers: conversation memory, users, audit log and retention."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import col, delete, select

from ben.config import Settings
from ben.core.models import SourceRef
from ben.storage.db import session_scope
from ben.storage.models import AuditLog, Conversation, Message, User, utcnow


class ConversationRepo:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _active(self, session, channel: str, chat_id: str, create: bool) -> Conversation | None:
        conv = session.exec(
            select(Conversation)
            .where(Conversation.channel == channel)
            .where(Conversation.chat_id == chat_id)
            .where(Conversation.active == True)  # noqa: E712
            .order_by(col(Conversation.id).desc())
        ).first()
        if conv is None and create:
            conv = Conversation(channel=channel, chat_id=chat_id)
            session.add(conv)
            session.commit()
            session.refresh(conv)
        return conv

    def history(self, channel: str, chat_id: str, turns: int) -> list[dict[str, str]]:
        """The last `turns` user/assistant pairs, oldest first, always starting with a user turn."""
        with session_scope(self.settings) as session:
            conv = self._active(session, channel, chat_id, create=False)
            if conv is None or turns <= 0:
                return []
            rows = session.exec(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(col(Message.id).desc())
                .limit(turns * 2)
            ).all()
        messages = [{"role": m.role, "content": m.content} for m in reversed(rows)]
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        return messages

    def append_exchange(
        self,
        channel: str,
        chat_id: str,
        user_text: str,
        assistant_text: str,
        sources: list[SourceRef],
    ) -> None:
        with session_scope(self.settings) as session:
            conv = self._active(session, channel, chat_id, create=True)
            session.add(Message(conversation_id=conv.id, role="user", content=user_text))
            session.add(Message(conversation_id=conv.id, role="assistant", content=assistant_text))
            conv.last_sources = [s.to_dict() for s in sources]
            conv.updated_at = utcnow()
            session.add(conv)
            session.commit()

    def reset(self, channel: str, chat_id: str) -> None:
        with session_scope(self.settings) as session:
            conv = self._active(session, channel, chat_id, create=False)
            if conv is not None:
                conv.active = False
                session.add(conv)
                session.commit()

    def last_sources(self, channel: str, chat_id: str) -> list[SourceRef]:
        with session_scope(self.settings) as session:
            conv = self._active(session, channel, chat_id, create=False)
            data = (conv.last_sources if conv else None) or []
        return [SourceRef.from_dict(d) for d in data]

    def touch_user(self, channel: str, external_id: str, display_name: str | None) -> None:
        with session_scope(self.settings) as session:
            user = session.exec(
                select(User).where(User.channel == channel).where(User.external_id == external_id)
            ).first()
            if user is None:
                user = User(channel=channel, external_id=external_id)
            user.display_name = display_name or user.display_name
            user.last_seen = utcnow()
            session.add(user)
            session.commit()

    def purge_older_than(self, days: int) -> int:
        """Delete conversations (and their messages) not updated in `days` days."""
        cutoff = utcnow() - timedelta(days=days)
        with session_scope(self.settings) as session:
            ids = session.exec(
                select(Conversation.id).where(Conversation.updated_at < cutoff)
            ).all()
            if ids:
                session.exec(delete(Message).where(col(Message.conversation_id).in_(ids)))
                session.exec(delete(Conversation).where(col(Conversation.id).in_(ids)))
                session.commit()
        return len(ids)


AUDIT_CSV_FIELDS = [
    "id",
    "timestamp",
    "channel",
    "user_id",
    "chat_id",
    "status",
    "question",
    "answer",
    "sources",
    "tool_calls",
    "unverified_ids",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "latency_ms",
    "redacted",
]


class AuditRepo:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def record(self, entry: AuditLog) -> None:
        with session_scope(self.settings) as session:
            session.add(entry)
            session.commit()

    def entries(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[AuditLog]:
        query = select(AuditLog).order_by(col(AuditLog.id))
        if since:
            query = query.where(AuditLog.timestamp >= since)
        if until:
            query = query.where(AuditLog.timestamp < until)
        with session_scope(self.settings) as session:
            return list(session.exec(query).all())

    def export_csv(
        self, out: Path, since: datetime | None = None, until: datetime | None = None
    ) -> int:
        rows = self.entries(since, until)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=AUDIT_CSV_FIELDS)
            writer.writeheader()
            for row in rows:
                data = row.model_dump()
                for key in ("sources", "tool_calls", "unverified_ids"):
                    data[key] = json.dumps(data[key], ensure_ascii=False) if data[key] else ""
                data["timestamp"] = row.timestamp.isoformat()
                writer.writerow({k: data.get(k) for k in AUDIT_CSV_FIELDS})
        return len(rows)

    def purge_older_than(self, days: int) -> int:
        cutoff = utcnow() - timedelta(days=days)
        with session_scope(self.settings) as session:
            result = session.exec(delete(AuditLog).where(AuditLog.timestamp < cutoff))
            session.commit()
            return result.rowcount or 0


def run_retention(settings: Settings) -> dict[str, int]:
    """Apply the configured retention periods. Returns the number of rows removed."""
    removed = {"conversations": 0, "audit_entries": 0}
    if settings.retention_days > 0:
        removed["conversations"] = ConversationRepo(settings).purge_older_than(
            settings.retention_days
        )
    if settings.audit_retention_days > 0:
        removed["audit_entries"] = AuditRepo(settings).purge_older_than(
            settings.audit_retention_days
        )
    return removed
