"""Who may talk to Ben. An empty allowlist denies everyone on that channel (secure default)."""

from __future__ import annotations

from ben.config import Settings
from ben.core.models import Channel


def normalise_number(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


def is_allowed(settings: Settings, channel: Channel, user_id: str) -> bool:
    if channel is Channel.CLI:
        return True  # local operator
    if channel is Channel.TELEGRAM:
        return user_id.isdigit() and int(user_id) in settings.telegram_allowed_user_ids
    if channel is Channel.WHATSAPP:
        allowed = {normalise_number(n) for n in settings.whatsapp_allowed_numbers}
        return normalise_number(user_id) in allowed
    return False
