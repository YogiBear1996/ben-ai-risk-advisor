from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ben.config import PROJECT_ROOT
from ben.knowledge.controls import load_controls, normalise_ai_type
from ben.knowledge.ingest import ingest
from ben.knowledge.library import KnowledgeLibrary
from ben.knowledge.loaders import load_document
from ben.knowledge.store import build_where, rrf_fuse
from ben.knowledge.types import SearchFilters
from ben.runtime import build_store

SAMPLE = PROJECT_ROOT / "knowledge"


@pytest.fixture
def kdir(tmp_path: Path) -> Path:
    target = tmp_path / "knowledge"
    shutil.copytree(SAMPLE, target)
    return target


@pytest.fixture
def library(settings, kdir):
    store = build_store(settings)
    ingest(kdir, settings, store)
    return KnowledgeLibrary(settings, store)


def test_loaders_extract_titles_sections_and_pages():
    pdf = load_document(SAMPLE / "sample_uae_pdpl_notes.pdf")
    assert pdf.title.startswith("UAE PDPL")
    pages = {s.heading: s.page for s in pdf.sections}
    assert pages["Section 3 Lawful basis and consent"] == 2
    docx = load_document(SAMPLE / "sample_iso42001_overview.docx")
    assert docx.title == "ISO/IEC 42001 Overview (Sample)"
    assert "Clause 6.1.4 AI system impact assessment" in [s.heading for s in docx.sections]
    md = load_document(SAMPLE / "sample_eu_ai_act_summary.md")
    assert any((s.heading or "").startswith("Article 50") for s in md.sections)


def test_control_library_is_parsed_as_structured_rows():
    controls = load_controls(SAMPLE / "ai_risk_control_library.xlsx")
    assert len(controls) == 10
    c = {c.control_id: c for c in controls}["AIRC-007"]
    assert c.ai_type == "GenAI"
    assert "RAG" in c.control_description


def test_control_columns_matched_flexibly(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Notes about this sheet"])
    ws.append(["Control ID", "Risk Category", "AI Type", "Control Description", "Region"])
    ws.append(["X-01", "Privacy", "generative", "Do the thing", "UAE"])
    wb.save(tmp_path / "c.xlsx")
    (control,) = load_controls(tmp_path / "c.xlsx")
    assert control.control_id == "X-01"
    assert control.ai_type == "GenAI"
    assert control.extra == {"Region": "UAE"}


def test_normalise_ai_type():
    assert normalise_ai_type("Traditional") == "Traditional"
    assert normalise_ai_type("Gen AI") == "GenAI"
    assert normalise_ai_type("both") == "Both"
    assert normalise_ai_type(None) is None


def test_ingest_is_idempotent_and_tracks_changes(settings, kdir):
    store = build_store(settings)
    first = ingest(kdir, settings, store)
    assert len(first.added) == 4 and not first.failed
    total = store.count()

    second = ingest(kdir, settings, store)
    assert second.changed == 0 and len(second.unchanged) == 4
    assert store.count() == total

    md = kdir / "sample_eu_ai_act_summary.md"
    md.write_text(md.read_text() + "\n## Article 99 — Penalties\n\nFines apply.\n")
    (kdir / "sample_uae_pdpl_notes.pdf").unlink()
    third = ingest(kdir, settings, store)
    assert third.updated == ["sample_eu_ai_act_summary.md"]
    assert third.removed == ["sample_uae_pdpl_notes.pdf"]
    lib = KnowledgeLibrary(settings, store)
    assert "UAE PDPL" not in " ".join(f.title for f in lib.list_frameworks())


def test_hybrid_search_finds_relevant_passages_with_metadata(library):
    hits = library.search("AI system impact assessment ISO 42001")
    assert hits
    assert any(h.source.section == "Clause 6.1.4 AI system impact assessment" for h in hits[:3])

    hits = library.search("CV screening recruitment high-risk")
    assert any("Annex III" in (h.source.section or "") for h in hits)

    hits = library.search("passenger consent lawful basis", SearchFilters(framework="PDPL"))
    assert hits and all("PDPL" in h.source.doc_title for h in hits)
    assert hits[0].source.page == 2


def test_filters_by_doc_type_and_ai_type(library):
    hits = library.search(
        "chatbot hallucination", SearchFilters(doc_type="control", ai_type="GenAI")
    )
    assert hits
    assert all(h.source.control_id for h in hits)
    assert all(h.ai_type in {"GenAI", "Both"} for h in hits)
    assert any(h.source.control_id == "AIRC-007" for h in hits)


def test_get_control_and_ids(library):
    assert library.get_control("airc-003").owner == "Use Case Owner"
    assert library.get_control("AIRC-999") is None
    assert "AIRC-010" in library.control_ids()
    titles = {f.title for f in library.list_frameworks()}
    assert "AI Risk & Control Library" in titles


def test_build_where_escapes_input():
    where = build_where(SearchFilters(framework="EU' OR 1=1 --", ai_type="GenAI"))
    assert "'%eu'' or 1=1 --%'" in where
    assert build_where(SearchFilters(doc_type="bogus")) is None


def test_rrf_prefers_items_ranked_well_in_both_lists():
    a = [{"id": "x"}, {"id": "y"}, {"id": "z"}]
    b = [{"id": "y"}, {"id": "q"}]
    assert [r["id"] for r in rrf_fuse([a, b])][0] == "y"
