"""Markdown -> platform formatting, and splitting long answers into message-sized chunks."""

from __future__ import annotations

import html
import re

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(
    r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])|(?<![_\w])_(?!\s)(.+?)(?<!\s)_(?![_\w])"
)
_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*]\s+", re.MULTILINE)


def split_message(text: str, limit: int) -> list[str]:
    """Split on paragraph, then line, then word boundaries so each part is <= limit chars."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    buf = ""
    for block in re.split(r"(\n\s*\n)", text):
        if len(buf) + len(block) <= limit:
            buf += block
            continue
        if buf.strip():
            parts.append(buf.strip())
        buf = ""
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            if cut <= 0:
                cut = block.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            parts.append(block[:cut].strip())
            block = block[cut:]
        buf = block
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


def _protect_code(text: str) -> tuple[str, list[str]]:
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    return _CODE.sub(stash, text), codes


def to_telegram_html(text: str) -> str:
    """Convert the simple Markdown Ben writes into Telegram's HTML parse mode."""
    text, codes = _protect_code(text)
    text = html.escape(text, quote=False)
    text = _HEADING.sub(r"<b>\1</b>", text)
    text = _BULLET.sub(r"\1• ", text)
    text = _LINK.sub(lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    return re.sub(
        r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", text
    )


def to_whatsapp(text: str) -> str:
    """Convert Markdown to WhatsApp's formatting (*bold*, _italic_, `code`)."""
    text, codes = _protect_code(text)
    text = _HEADING.sub(r"**\1**", text)
    text = _BULLET.sub(r"\1• ", text)
    text = _LINK.sub(r"\1 (\2)", text)
    text = _ITALIC.sub(lambda m: f"_{m.group(1) or m.group(2)}_", text)
    text = _BOLD.sub(lambda m: f"*{m.group(1) or m.group(2)}*", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"`{codes[int(m.group(1))]}`", text)


def strip_markdown(text: str) -> str:
    text, codes = _protect_code(text)
    text = _HEADING.sub(r"\1", text)
    text = _BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _ITALIC.sub(lambda m: m.group(1) or m.group(2), text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)
