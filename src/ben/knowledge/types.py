"""Lightweight data types for the knowledge layer (no heavy imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ben.core.models import SourceRef


@dataclass(frozen=True)
class SearchFilters:
    framework: str | None = None  # substring match on document title / framework
    ai_type: str | None = None  # Traditional | GenAI | Both
    doc_type: str | None = None  # "document" | "control"


@dataclass(frozen=True)
class Passage:
    text: str
    source: SourceRef
    score: float = 0.0
    ai_type: str | None = None


@dataclass(frozen=True)
class ControlRecord:
    control_id: str
    risk_category: str | None = None
    ai_type: str | None = None
    risk_description: str | None = None
    control_description: str | None = None
    owner: str | None = None
    framework_mapping: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None

    def as_text(self) -> str:
        lines = [f"Control ID: {self.control_id}"]
        for label, value in [
            ("Risk category", self.risk_category),
            ("AI type", self.ai_type),
            ("Risk description", self.risk_description),
            ("Control description", self.control_description),
            ("Owner", self.owner),
            ("Framework mapping", self.framework_mapping),
            *self.extra.items(),
        ]:
            if value:
                lines.append(f"{label}: {value}")
        return "\n".join(lines)


@dataclass(frozen=True)
class FrameworkInfo:
    title: str
    source_path: str
    doc_type: str
    chunks: int


class Library(Protocol):
    """What the agent's tools need from the knowledge layer."""

    def search(
        self, query: str, filters: SearchFilters | None = None, top_k: int | None = None
    ) -> list[Passage]: ...

    def get_control(self, control_id: str) -> ControlRecord | None: ...

    def list_frameworks(self) -> list[FrameworkInfo]: ...

    def control_ids(self) -> set[str]: ...
