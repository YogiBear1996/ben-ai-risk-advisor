"""Idempotent ingestion: only new or changed files are (re)processed; deleted files are removed."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import delete, select

from ben.config import Settings
from ben.knowledge.chunker import chunk_sections
from ben.knowledge.controls import CONTROL_LIBRARY_TITLE, load_controls
from ben.knowledge.loaders import SUPPORTED_EXTENSIONS, load_document
from ben.knowledge.store import ChunkRow, VectorStore
from ben.storage.db import session_scope
from ben.storage.models import Control, IngestedFile, utcnow

log = logging.getLogger(__name__)


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    chunks: int = 0

    @property
    def changed(self) -> int:
        return len(self.added) + len(self.updated) + len(self.removed)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def discover(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and not p.name.startswith((".", "~$"))
        and p.name.lower() != "readme.md"
    )


def _chunk_id(rel: str, index: int) -> str:
    return hashlib.sha1(f"{rel}#{index}".encode(), usedforsecurity=False).hexdigest()[:16]


def build_rows(path: Path, rel: str, settings: Settings) -> tuple[str, str, list[ChunkRow], list]:
    """Load one file -> (title, doc_type, chunk rows, control records)."""
    if path.suffix.lower() == ".xlsx":
        controls = load_controls(path)
        if controls:
            rows = [
                ChunkRow(
                    id=_chunk_id(rel, i),
                    text=c.as_text(),
                    search_text=f"{CONTROL_LIBRARY_TITLE} | {c.control_id}\n{c.as_text()}",
                    doc_title=CONTROL_LIBRARY_TITLE,
                    section=c.control_id,
                    page=None,
                    source_path=rel,
                    doc_type="control",
                    control_id=c.control_id,
                    ai_type=c.ai_type,
                )
                for i, c in enumerate(controls)
            ]
            return CONTROL_LIBRARY_TITLE, "control", rows, controls

    doc = load_document(path)
    chunks = chunk_sections(doc.sections, settings.chunk_max_chars, settings.chunk_overlap_chars)
    rows = [
        ChunkRow(
            id=_chunk_id(rel, c.index),
            text=c.text,
            search_text=f"{doc.title} | {c.section or ''}\n{c.text}",
            doc_title=doc.title,
            section=c.section,
            page=c.page,
            source_path=rel,
            doc_type="document",
            control_id=None,
            ai_type=None,
        )
        for c in chunks
    ]
    return doc.title, "document", rows, []


def ingest(root: Path, settings: Settings, store: VectorStore, force: bool = False) -> IngestReport:
    report = IngestReport()
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Knowledge directory not found: {root}")

    recreated = store.ensure_table()
    embedder_name = store.embedder.name

    with session_scope(settings) as session:
        manifest = {f.path: f for f in session.exec(select(IngestedFile)).all()}
        # Embedding model changed or index rebuilt: everything must be re-embedded.
        rebuild_all = (
            force or recreated or any(f.embedder != embedder_name for f in manifest.values())
        )

        seen: set[str] = set()
        for path in discover(root):
            rel = path.relative_to(root).as_posix()
            seen.add(rel)
            digest = file_sha256(path)
            existing = manifest.get(rel)
            if existing and existing.sha256 == digest and not rebuild_all:
                report.unchanged.append(rel)
                continue
            try:
                title, doc_type, rows, controls = build_rows(path, rel, settings)
            except Exception as exc:
                log.exception("Failed to load %s", rel)
                report.failed[rel] = f"{type(exc).__name__}: {exc}"
                continue

            store.delete_source(rel)
            store.add(rows)
            session.exec(delete(Control).where(Control.source_path == rel))
            for c in controls:
                session.merge(
                    Control(
                        control_id=c.control_id,
                        risk_category=c.risk_category,
                        ai_type=c.ai_type,
                        risk_description=c.risk_description,
                        control_description=c.control_description,
                        owner=c.owner,
                        framework_mapping=c.framework_mapping,
                        extra=c.extra or None,
                        source_path=rel,
                    )
                )
            record = existing or IngestedFile(path=rel, sha256="", title=title, doc_type=doc_type)
            record.sha256, record.embedder = digest, embedder_name
            record.title, record.doc_type = title, doc_type
            record.chunks, record.ingested_at = len(rows), utcnow()
            session.add(record)
            session.commit()
            report.chunks += len(rows)
            (report.updated if existing else report.added).append(rel)
            log.info("Ingested %s: %d chunks", rel, len(rows))

        for rel, record in manifest.items():
            if rel not in seen:
                store.delete_source(rel)
                session.exec(delete(Control).where(Control.source_path == rel))
                session.delete(record)
                session.commit()
                report.removed.append(rel)

    if report.changed:
        store.rebuild_fts()
    return report
