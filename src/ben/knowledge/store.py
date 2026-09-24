"""Vector + full-text store on LanceDB, with hybrid search via Reciprocal Rank Fusion.

LanceDB is embedded (a directory on disk, no server) and has a native BM25 full-text index, so
hybrid retrieval needs no extra service.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa
from lancedb.index import FTS

from ben.core.models import SourceRef
from ben.knowledge.embeddings import Embedder
from ben.knowledge.types import Passage, SearchFilters

log = logging.getLogger(__name__)

TABLE = "chunks"
RRF_K = 60


@dataclass(frozen=True)
class ChunkRow:
    id: str
    text: str
    search_text: str
    doc_title: str
    section: str | None
    page: int | None
    source_path: str
    doc_type: str  # document | control
    control_id: str | None
    ai_type: str | None


def _schema(dim: int) -> pa.Schema:
    return pa.schema(
        [
            ("id", pa.string()),
            ("text", pa.string()),
            ("search_text", pa.string()),
            ("doc_title", pa.string()),
            ("section", pa.string()),
            ("page", pa.int32()),
            ("source_path", pa.string()),
            ("doc_type", pa.string()),
            ("control_id", pa.string()),
            ("ai_type", pa.string()),
            ("vector", pa.list_(pa.float32(), dim)),
        ]
    )


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _like_literal(value: str) -> str:
    cleaned = re.sub(r"[%_\\]", " ", value.lower()).strip()
    return _sql_str(f"%{cleaned}%")


def build_where(filters: SearchFilters | None) -> str | None:
    if not filters:
        return None
    clauses = []
    if filters.framework:
        clauses.append(f"lower(doc_title) LIKE {_like_literal(filters.framework)}")
    if filters.doc_type in {"document", "control"}:
        clauses.append(f"doc_type = {_sql_str(filters.doc_type)}")
    if filters.ai_type in {"Traditional", "GenAI", "Both"}:
        types = ", ".join(_sql_str(t) for t in {filters.ai_type, "Both"})
        clauses.append(f"(doc_type = 'document' OR ai_type IS NULL OR ai_type IN ({types}))")
    return " AND ".join(f"({c})" for c in clauses) if clauses else None


def _fts_query(query: str) -> str:
    """Strip query-syntax characters so user text can't break the FTS parser."""
    return " ".join(re.findall(r"[A-Za-z0-9]+", query))


def rrf_fuse(ranked_lists: list[list[dict[str, Any]]], k: int = RRF_K) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    rows: dict[str, dict[str, Any]] = {}
    for ranked in ranked_lists:
        for rank, row in enumerate(ranked):
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (k + rank + 1)
            rows.setdefault(row["id"], row)
    ordered = sorted(scores, key=lambda i: scores[i], reverse=True)
    return [{**rows[i], "_rrf": scores[i]} for i in ordered]


class VectorStore:
    def __init__(self, path: Path, embedder: Embedder) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(path))
        self.embedder = embedder
        self._table = None

    # --- table management ---
    def _open(self):
        if self._table is None:
            try:
                self._table = self.db.open_table(TABLE)
            except (FileNotFoundError, ValueError):
                return None
        return self._table

    def ensure_table(self) -> bool:
        """Create the table if needed. Returns True if it was (re)created empty."""
        table = self._open()
        if table is not None:
            dim = table.schema.field("vector").type.list_size
            if dim == self.embedder.dim:
                return False
            log.warning(
                "Embedding dimension changed (%s -> %s); rebuilding index", dim, self.embedder.dim
            )
            self.db.drop_table(TABLE)
        self._table = self.db.create_table(TABLE, schema=_schema(self.embedder.dim))
        return True

    def count(self) -> int:
        table = self._open()
        return table.count_rows() if table is not None else 0

    def delete_source(self, source_path: str) -> None:
        table = self._open()
        if table is not None:
            table.delete(f"source_path = {_sql_str(source_path)}")

    def add(self, rows: list[ChunkRow]) -> None:
        if not rows:
            return
        vectors = self.embedder.embed_documents([r.search_text for r in rows])
        self._open().add([{**r.__dict__, "vector": v} for r, v in zip(rows, vectors, strict=True)])

    def rebuild_fts(self) -> None:
        table = self._open()
        if table is not None and table.count_rows() > 0:
            table.create_index("search_text", config=FTS(), replace=True)

    # --- search ---
    def _vector_search(self, query: str, where: str | None, limit: int) -> list[dict[str, Any]]:
        q = self._open().search(self.embedder.embed_query(query))
        if where:
            q = q.where(where, prefilter=True)
        return q.limit(limit).to_list()

    def _fts_search(self, query: str, where: str | None, limit: int) -> list[dict[str, Any]]:
        text = _fts_query(query)
        if not text:
            return []
        try:
            q = self._open().search(text, query_type="fts")
            if where:
                q = q.where(where, prefilter=True)
            return q.limit(limit).to_list()
        except Exception:  # missing index or parser edge case: fall back to vectors only
            log.warning("Full-text search failed; using vector search only", exc_info=True)
            return []

    def search(
        self, query: str, filters: SearchFilters | None = None, top_k: int = 6, hybrid: bool = True
    ) -> list[Passage]:
        if self._open() is None or self.count() == 0:
            return []
        where = build_where(filters)
        candidates = top_k * 3
        vector_hits = self._vector_search(query, where, candidates)
        if hybrid:
            rows = rrf_fuse([vector_hits, self._fts_search(query, where, candidates)])
        else:
            rows = [{**r, "_rrf": 1.0 / (RRF_K + i + 1)} for i, r in enumerate(vector_hits)]
        return [
            Passage(
                text=r["text"],
                source=SourceRef(
                    doc_title=r["doc_title"],
                    section=r.get("section"),
                    page=r.get("page"),
                    source_path=r.get("source_path"),
                    chunk_id=r["id"],
                    control_id=r.get("control_id"),
                ),
                score=r["_rrf"],
                ai_type=r.get("ai_type"),
            )
            for r in rows[:top_k]
        ]
