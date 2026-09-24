"""KnowledgeLibrary: the Library implementation backed by LanceDB + SQL."""

from __future__ import annotations

from sqlalchemy import func
from sqlmodel import select

from ben.config import Settings
from ben.knowledge.store import VectorStore
from ben.knowledge.types import ControlRecord, FrameworkInfo, Passage, SearchFilters
from ben.storage.db import session_scope
from ben.storage.models import Control, IngestedFile


class KnowledgeLibrary:
    def __init__(self, settings: Settings, store: VectorStore) -> None:
        self.settings = settings
        self.store = store

    def search(
        self, query: str, filters: SearchFilters | None = None, top_k: int | None = None
    ) -> list[Passage]:
        return self.store.search(
            query,
            filters,
            top_k or self.settings.retrieval_top_k,
            hybrid=self.settings.hybrid_search,
        )

    def get_control(self, control_id: str) -> ControlRecord | None:
        key = control_id.strip().upper()
        with session_scope(self.settings) as session:
            row = session.exec(select(Control).where(func.upper(Control.control_id) == key)).first()
        if row is None:
            return None
        return ControlRecord(
            control_id=row.control_id,
            risk_category=row.risk_category,
            ai_type=row.ai_type,
            risk_description=row.risk_description,
            control_description=row.control_description,
            owner=row.owner,
            framework_mapping=row.framework_mapping,
            extra=row.extra or {},
            source_path=row.source_path,
        )

    def list_frameworks(self) -> list[FrameworkInfo]:
        with session_scope(self.settings) as session:
            rows = session.exec(select(IngestedFile).order_by(IngestedFile.title)).all()
        return [FrameworkInfo(r.title, r.path, r.doc_type, r.chunks) for r in rows]

    def control_ids(self) -> set[str]:
        with session_scope(self.settings) as session:
            return set(session.exec(select(Control.control_id)).all())
