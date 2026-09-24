"""Persistence helpers: conversation memory, users, audit log and retention."""

from __future__ import annotations

from datetime import timedelta

from sqlmodel import col, delete, select

from ben.config import Settings
from ben.core.models import SourceRef
from ben.storage.db import session_scope
from ben.storage.models import Conversation, Message, User, utcnow


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
