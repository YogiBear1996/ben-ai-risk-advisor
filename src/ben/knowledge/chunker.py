"""Heading/clause-aware chunking.

Each section (heading, article or clause) becomes one chunk if it fits; long sections are split
on paragraph then sentence boundaries with a small overlap. Chunks never span two sections, so
every chunk has a single, citable heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ben.knowledge.loaders import Section

_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+")


@dataclass(frozen=True)
class Chunk:
    text: str
    section: str | None
    page: int | None
    index: int


def _pieces(text: str, max_chars: int) -> list[str]:
    """Split text into paragraph pieces, breaking oversized paragraphs by sentence."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            out.append(para)
            continue
        buf = ""
        for sentence in _SENTENCE_RE.split(para):
            while len(sentence) > max_chars:  # pathological: no punctuation
                out.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            if buf and len(buf) + len(sentence) + 1 > max_chars:
                out.append(buf)
                buf = sentence
            else:
                buf = f"{buf} {sentence}".strip()
        if buf:
            out.append(buf)
    return out


def _tail(text: str, overlap: int) -> str:
    """The last ~`overlap` characters of `text`, starting at a word boundary."""
    if overlap <= 0:
        return ""
    if len(text) <= overlap:
        return text
    cut = text[-overlap:]
    space = cut.find(" ")
    return cut[space + 1 :] if space != -1 else cut


def chunk_sections(
    sections: list[Section], max_chars: int = 1800, overlap: int = 200
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        text = section.text.strip()
        if not text:
            continue
        if len(text) <= max_chars:
            chunks.append(Chunk(text, section.heading, section.page, len(chunks)))
            continue
        buf = ""
        for piece in _pieces(text, max_chars):
            candidate = f"{buf}\n\n{piece}" if buf else piece
            if len(candidate) <= max_chars:
                buf = candidate
                continue
            if buf:
                chunks.append(Chunk(buf, section.heading, section.page, len(chunks)))
                tail = _tail(buf, overlap)
                buf = f"{tail}\n\n{piece}" if tail and len(tail) + len(piece) < max_chars else piece
            else:
                buf = piece
        if buf:
            chunks.append(Chunk(buf, section.heading, section.page, len(chunks)))
    return chunks
