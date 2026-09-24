"""Tool definitions exposed to Claude, and their handlers.

Retrieved text is returned inside clearly delimited `<retrieved_data>` blocks so the model can
tell library content (data) apart from instructions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ben.core.models import SourceRef
from ben.knowledge.types import Library, SearchFilters

log = logging.getLogger(__name__)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_library",
        "description": (
            "Search Ben's private document library (regulations, standards, internal policies "
            "and the AI Risk & Control Library) and return the most relevant passages with "
            "their source metadata (document title, section/clause, page). Use this before "
            "answering any substantive question. Run it several times with different wording "
            "if results are thin."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query, e.g. 'AI impact assessment'.",
                },
                "filters": {
                    "type": "object",
                    "description": "Optional filters to narrow the search.",
                    "properties": {
                        "framework": {
                            "type": "string",
                            "description": "Restrict to documents whose title contains this "
                            "text, e.g. 'EU AI Act', '42001', 'PDPL'.",
                        },
                        "ai_type": {
                            "type": "string",
                            "enum": ["Traditional", "GenAI", "Both"],
                            "description": "Restrict controls to this AI type ('Both' "
                            "controls are always included).",
                        },
                        "doc_type": {
                            "type": "string",
                            "enum": ["document", "control"],
                            "description": "'control' searches only the control library; "
                            "'document' only regulations/standards/policies.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_control",
        "description": (
            "Look up one entry in the AI Risk & Control Library by its exact control ID and "
            "return all of its fields (risk category, AI type, risk, control, owner, framework "
            "mapping)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "control_id": {"type": "string", "description": "Exact control ID."},
            },
            "required": ["control_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_frameworks",
        "description": "List the frameworks, regulations and documents loaded in the library.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


@dataclass
class ToolOutput:
    content: str
    sources: list[SourceRef] = field(default_factory=list)
    is_error: bool = False


def _escape(text: str) -> str:
    """Stop retrieved text from closing our delimiters early."""
    return (
        text.replace("</document", "&lt;/document")
        .replace("</control", "&lt;/control")
        .replace("</retrieved_data", "&lt;/retrieved_data")
    )


def _attr(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


class ToolBox:
    def __init__(self, library: Library, top_k: int = 6) -> None:
        self.library = library
        self.top_k = top_k

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return TOOL_DEFINITIONS

    def execute(self, name: str, tool_input: dict[str, Any]) -> ToolOutput:
        try:
            if name == "search_library":
                return self._search(tool_input)
            if name == "get_control":
                return self._get_control(tool_input)
            if name == "list_frameworks":
                return self._list_frameworks()
        except Exception as exc:  # a failed tool must not crash the conversation
            log.exception("Tool %s failed", name)
            return ToolOutput(f"Tool error: {type(exc).__name__}", is_error=True)
        return ToolOutput(f"Unknown tool: {name}", is_error=True)

    def _search(self, tool_input: dict[str, Any]) -> ToolOutput:
        query = str(tool_input.get("query", "")).strip()
        if not query:
            return ToolOutput("query must not be empty", is_error=True)
        raw_filters = tool_input.get("filters") or {}
        filters = SearchFilters(
            framework=raw_filters.get("framework"),
            ai_type=raw_filters.get("ai_type"),
            doc_type=raw_filters.get("doc_type"),
        )
        passages = self.library.search(query, filters, self.top_k)
        if not passages:
            return ToolOutput(
                "<retrieved_data>\nNo matching passages found in the library.\n</retrieved_data>"
            )
        blocks = []
        for i, p in enumerate(passages, start=1):
            src = p.source
            attrs = [f"index={_attr(i)}", f"source={_attr(src.doc_title)}"]
            if src.control_id:
                attrs.append(f"control_id={_attr(src.control_id)}")
            if src.section:
                attrs.append(f"section={_attr(src.section)}")
            if src.page:
                attrs.append(f"page={_attr(src.page)}")
            if p.ai_type:
                attrs.append(f"ai_type={_attr(p.ai_type)}")
            blocks.append(f"<document {' '.join(attrs)}>\n{_escape(p.text)}\n</document>")
        body = "\n".join(blocks)
        return ToolOutput(
            f"<retrieved_data>\n{body}\n</retrieved_data>", sources=[p.source for p in passages]
        )

    def _get_control(self, tool_input: dict[str, Any]) -> ToolOutput:
        control_id = str(tool_input.get("control_id", "")).strip()
        control = self.library.get_control(control_id)
        if control is None:
            return ToolOutput(
                f"<retrieved_data>\nNo control with ID {_attr(control_id)} exists in the "
                "library.\n</retrieved_data>"
            )
        source = SourceRef(
            doc_title="AI Risk & Control Library",
            control_id=control.control_id,
            source_path=control.source_path,
        )
        return ToolOutput(
            f"<retrieved_data>\n<control id={_attr(control.control_id)}>\n"
            f"{_escape(control.as_text())}\n</control>\n</retrieved_data>",
            sources=[source],
        )

    def _list_frameworks(self) -> ToolOutput:
        items = self.library.list_frameworks()
        if not items:
            return ToolOutput("The library is empty - nothing has been ingested yet.")
        lines = [f"- {f.title} ({f.doc_type}, {f.chunks} passages)" for f in items]
        return ToolOutput("Loaded documents:\n" + "\n".join(lines))
