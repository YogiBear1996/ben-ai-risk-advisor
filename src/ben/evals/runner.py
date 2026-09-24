"""`ben eval`: run the question set and score citation accuracy and groundedness.

Two modes:
- full (needs an Anthropic API key): runs Ben end-to-end on each question.
- retrieval-only: checks that the expected sources are retrieved; no model calls.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from ben.core.agent import BenAgent
from ben.core.models import AgentResult, SourceRef
from ben.knowledge.types import Library

DECLINE_MARKERS = [
    "doesn't cover",
    "does not cover",
    "not covered",
    "isn't covered",
    "is not covered",
    "no information",
    "not in the library",
    "isn't in the library",
    "not in my library",
    "general knowledge",
    "can't share",
    "cannot share",
    "can't help",
    "not able to",
    "unable to",
    "won't",
    "don't have",
]
CITATION_RE = re.compile(r"\[([^\[\]]{3,200})\](?!\()")


@dataclass
class EvalCase:
    id: str
    question: str
    expected_sources: list[str] = field(default_factory=list)
    expected_sections: list[str] = field(default_factory=list)
    expected_control_ids: list[str] = field(default_factory=list)
    key_points: list[str] = field(default_factory=list)
    should_decline: bool = False


@dataclass
class CaseResult:
    id: str
    passed: bool
    citation_accuracy: float | None = None
    retrieval_recall: float = 0.0
    grounded: bool | None = None
    key_point_coverage: float | None = None
    declined_correctly: bool | None = None
    unverified_ids: list[str] = field(default_factory=list)
    ungrounded_citations: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    judge: dict[str, Any] | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    answer: str | None = None


def load_cases(path: Path) -> list[EvalCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    fields = EvalCase.__dataclass_fields__
    return [EvalCase(**{k: v for k, v in item.items() if k in fields}) for item in data]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).lower()


def _core_title(title: str) -> str:
    return _norm(re.sub(r"\(sample\)", "", title, flags=re.I)).strip(" -—")


def _source_matches(alias: str, source: SourceRef) -> bool:
    a = _norm(alias)
    return a in _norm(source.doc_title) or a == _norm(source.control_id or "")


def _section_matches(section: str, source: SourceRef) -> bool:
    return _norm(section) in _norm(source.section or "")


def retrieval_recall(case: EvalCase, sources: list[SourceRef]) -> tuple[float, list[str]]:
    expected = (
        [("source", s) for s in case.expected_sources]
        + [("section", s) for s in case.expected_sections]
        + [("control", c) for c in case.expected_control_ids]
    )
    if not expected:
        return 1.0, []
    missing = []
    for kind, value in expected:
        if kind == "section":
            ok = any(_section_matches(value, s) for s in sources)
        else:
            ok = any(_source_matches(value, s) for s in sources)
        if not ok:
            missing.append(f"retrieved {kind}: {value}")
    return 1 - len(missing) / len(expected), missing


def citation_accuracy(
    case: EvalCase, answer: str, sources: list[SourceRef]
) -> tuple[float | None, list[str]]:
    """Share of expected sources/controls that were both retrieved and cited in the answer."""
    expected = case.expected_sources + case.expected_control_ids
    if not expected:
        return None, []
    text = _norm(answer)
    missing = []
    for alias in expected:
        retrieved = any(_source_matches(alias, s) for s in sources)
        cited = _norm(alias) in text or any(
            _source_matches(alias, s) and _core_title(s.doc_title) in text for s in sources
        )
        if not (retrieved and cited):
            missing.append(f"cited: {alias}")
    return 1 - len(missing) / len(expected), missing


def ungrounded_citations(answer: str, sources: list[SourceRef]) -> list[str]:
    """Bracketed citations in the answer that don't match anything the tools returned."""
    bad = []
    for citation in CITATION_RE.findall(answer):
        c = _norm(citation)
        if not re.search(r"[a-z]", c):
            continue
        matched = any(
            _core_title(s.doc_title) in c
            or c.split(" — ")[0].split(" - ")[0].strip() in _norm(s.doc_title)
            or (s.control_id and _norm(s.control_id) in c)
            for s in sources
        )
        if not matched:
            bad.append(citation)
    return bad


def key_point_coverage(case: EvalCase, answer: str) -> tuple[float | None, list[str]]:
    if not case.key_points:
        return None, []
    text = _norm(answer)
    missing = [kp for kp in case.key_points if not any(_norm(opt) in text for opt in kp.split("|"))]
    return 1 - len(missing) / len(case.key_points), [f"key point: {m}" for m in missing]


def declined(answer: str) -> bool:
    text = _norm(answer)
    return any(marker in text for marker in DECLINE_MARKERS)


def score_answer(case: EvalCase, result: AgentResult) -> CaseResult:
    sources = result.sources
    recall, missing = retrieval_recall(case, sources)
    cites, miss_c = citation_accuracy(case, result.text, sources)
    kp, miss_k = key_point_coverage(case, result.text)
    bad_cites = ungrounded_citations(result.text, sources)
    has_citation = bool(CITATION_RE.search(result.text)) or bool(case.expected_control_ids)
    grounded = not result.unverified_ids and not bad_cites and (case.should_decline or has_citation)
    declined_ok = declined(result.text) if case.should_decline else None
    passed = grounded and (declined_ok if case.should_decline else (cites or 0) >= 0.5)
    return CaseResult(
        id=case.id,
        passed=bool(passed),
        citation_accuracy=cites,
        retrieval_recall=recall,
        grounded=grounded,
        key_point_coverage=kp,
        declined_correctly=declined_ok,
        unverified_ids=result.unverified_ids,
        ungrounded_citations=bad_cites,
        missing=missing + miss_c + miss_k,
        latency_ms=result.latency_ms,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        answer=result.text,
    )


class JudgeVerdict(BaseModel):
    grounded: bool
    unsupported_claims: list[str]
    key_points_covered: list[str]
    notes: str


JUDGE_PROMPT = """You are grading an AI governance assistant's answer for groundedness.

<question>{question}</question>
<retrieved_sources>
{sources}
</retrieved_sources>
<answer>{answer}</answer>
<expected_key_points>{key_points}</expected_key_points>

grounded = every substantive claim in the answer is supported by the retrieved sources, or is
explicitly labelled as general knowledge. List any unsupported claims, and which expected key
points the answer covers."""


def judge_answer(client: Any, model: str, case: EvalCase, result: AgentResult, library: Library):
    passages = []
    for s in result.sources:
        hits = library.search(f"{s.doc_title} {s.section or s.control_id or ''}", top_k=1)
        passages.append(f"[{s.label()}] {hits[0].text if hits else ''}")
    response = client.messages.parse(
        model=model,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": JUDGE_PROMPT.format(
                    question=case.question,
                    sources="\n\n".join(passages) or "(none)",
                    answer=result.text,
                    key_points=", ".join(case.key_points) or "(none)",
                ),
            }
        ],
        output_format=JudgeVerdict,
    )
    return response.parsed_output.model_dump() if response.parsed_output else None


def run_full(
    cases: list[EvalCase], agent: BenAgent, judge: bool = False, on_result=None
) -> list[CaseResult]:
    results = []
    for case in cases:
        answer = agent.respond([], case.question)
        scored = score_answer(case, answer)
        if judge and answer.stop_reason != "error":
            scored.judge = judge_answer(
                agent.client, agent.model, case, answer, agent.toolbox.library
            )
        results.append(scored)
        if on_result:
            on_result(scored)
    return results


def run_retrieval_only(
    cases: list[EvalCase], library: Library, top_k: int, on_result=None
) -> list[CaseResult]:
    results = []
    for case in cases:
        sources = [p.source for p in library.search(case.question, top_k=top_k)]
        recall, missing = retrieval_recall(case, sources)
        passed = recall >= 0.5 if not case.should_decline else True
        result = CaseResult(id=case.id, passed=passed, retrieval_recall=recall, missing=missing)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def summarise(results: list[CaseResult], mode: str, model: str | None) -> dict[str, Any]:
    summary = {
        "mode": mode,
        "model": model,
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "retrieval_recall": _mean([r.retrieval_recall for r in results]),
    }
    if mode == "full":
        summary.update(
            citation_accuracy=_mean([r.citation_accuracy for r in results]),
            grounded_rate=_mean([float(r.grounded) for r in results if r.grounded is not None]),
            key_point_coverage=_mean([r.key_point_coverage for r in results]),
            decline_accuracy=_mean(
                [float(r.declined_correctly) for r in results if r.declined_correctly is not None]
            ),
            answers_with_unverified_ids=sum(bool(r.unverified_ids) for r in results),
            avg_latency_ms=_mean([float(r.latency_ms) for r in results]),
            total_input_tokens=sum(r.input_tokens for r in results),
            total_output_tokens=sum(r.output_tokens for r in results),
        )
        judged = [r.judge for r in results if r.judge]
        if judged:
            summary["judge_grounded_rate"] = _mean([float(j["grounded"]) for j in judged])
    return summary


def write_report(path: Path, summary: dict[str, Any], results: list[CaseResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "results": [asdict(r) for r in results]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
