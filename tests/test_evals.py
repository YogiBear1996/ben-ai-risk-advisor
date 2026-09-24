from __future__ import annotations

from ben.config import PROJECT_ROOT
from ben.core.models import AgentResult, SourceRef, Usage
from ben.evals import runner
from ben.runtime import build_agent
from tests.fakes import FakeAnthropic, text_response, tool_response
from tests.test_agent import StubLibrary

ART6 = SourceRef("EU AI Act Summary (Sample)", "Article 6 — Classification", 3)
CTRL = SourceRef("AI Risk & Control Library", control_id="AIRC-002")


def result(text, sources, unverified=()):
    return AgentResult(text, list(sources), Usage(), "m", 10, unverified_ids=list(unverified))


def case(**kw):
    base = {"id": "c", "question": "q"}
    return runner.EvalCase(**{**base, **kw})


def test_question_file_loads_and_is_well_formed():
    cases = runner.load_cases(PROJECT_ROOT / "evals" / "questions.yaml")
    assert len(cases) >= 20
    assert len({c.id for c in cases}) == len(cases)
    assert any(c.should_decline for c in cases)


def test_grounded_answer_with_correct_citations_passes():
    c = case(
        expected_sources=["EU AI Act"],
        expected_sections=["Article 6"],
        expected_control_ids=["AIRC-002"],
        key_points=["high-risk", "employment|recruitment"],
    )
    text = (
        "Yes, it's **high-risk** because recruitment is listed "
        "[EU AI Act Summary — Article 6, p.3]. Classify it under `AIRC-002` "
        "[AI Risk & Control Library — AIRC-002]."
    )
    r = runner.score_answer(c, result(text, [ART6, CTRL]))
    assert r.passed and r.grounded
    assert r.citation_accuracy == 1.0 and r.retrieval_recall == 1.0 and r.key_point_coverage == 1.0


def test_invented_citation_or_control_id_is_not_grounded():
    c = case(expected_sources=["EU AI Act"])
    text = "See [EU AI Act Summary — Article 6] and [GDPR — Art. 22]."
    r = runner.score_answer(c, result(text, [ART6]))
    assert not r.grounded and r.ungrounded_citations == ["GDPR — Art. 22"]

    r = runner.score_answer(c, result("[EU AI Act Summary] says AIRC-777.", [ART6], ["AIRC-777"]))
    assert not r.grounded and not r.passed


def test_uncited_answer_fails_citation_accuracy():
    c = case(expected_sources=["EU AI Act"])
    r = runner.score_answer(c, result("It is high-risk.", [ART6]))
    assert r.citation_accuracy == 0.0 and not r.passed


def test_decline_cases():
    c = case(should_decline=True)
    assert runner.score_answer(c, result("The library doesn't cover this.", [])).passed
    assert not runner.score_answer(c, result("Sure! Here is everything.", [])).passed


def test_full_run_with_scripted_model(settings):
    client = FakeAnthropic(
        [
            tool_response("search_library", {"query": "high risk"}),
            text_response("High-risk [EU AI Act summary — Art. 6]."),
        ]
    )
    agent = build_agent(settings, library=StubLibrary(), client=client)
    cases = [case(expected_sources=["EU AI Act"], key_points=["high-risk"])]
    results = runner.run_full(cases, agent)
    summary = runner.summarise(results, "full", "m")
    assert summary["passed"] == 1 and summary["citation_accuracy"] == 1.0
    assert summary["grounded_rate"] == 1.0


def test_retrieval_only_run_on_sample_library(settings, tmp_path):
    import shutil

    from ben.knowledge.ingest import ingest
    from ben.knowledge.library import KnowledgeLibrary
    from ben.runtime import build_store

    kdir = tmp_path / "k"
    shutil.copytree(PROJECT_ROOT / "knowledge", kdir)
    store = build_store(settings)
    ingest(kdir, settings, store)
    library = KnowledgeLibrary(settings, store)
    cases = runner.load_cases(PROJECT_ROOT / "evals" / "questions.yaml")
    results = runner.run_retrieval_only(cases, library, top_k=6)
    summary = runner.summarise(results, "retrieval", None)
    assert summary["retrieval_recall"] >= 0.8
    out = tmp_path / "r.json"
    runner.write_report(out, summary, results)
    assert out.read_text().startswith("{")
