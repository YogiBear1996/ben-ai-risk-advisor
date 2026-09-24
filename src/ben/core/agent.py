"""BenAgent: takes a conversation and returns a grounded reply using Claude + tool use."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import anthropic

from ben.config import Settings
from ben.core.models import AgentResult, SourceRef, Usage
from ben.core.tools import ToolBox

log = logging.getLogger(__name__)

# Things that look like control IDs (e.g. AIRC-004). Used to flag IDs the model mentions that
# don't exist in the library - a hallucination signal recorded in the audit log and evals.
CONTROL_ID_PATTERN = re.compile(r"\b[A-Z]{2,6}-\d{2,4}\b")

REFUSAL_TEXT = (
    "Sorry - I can't help with that request. If you think this is a mistake, try rephrasing, "
    "or contact the AI governance team."
)
ERROR_TEXT = "Sorry - I hit a problem reaching my language model. Please try again in a moment."


def _dedupe(sources: list[SourceRef]) -> list[SourceRef]:
    seen: set[tuple] = set()
    out: list[SourceRef] = []
    for s in sources:
        key = (s.doc_title, s.section, s.page, s.control_id)
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


class BenAgent:
    def __init__(
        self,
        settings: Settings,
        toolbox: ToolBox,
        system_prompt: str,
        client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.toolbox = toolbox
        self.system_prompt = system_prompt
        self.model = settings.ben_model
        if client is None:
            key = settings.anthropic_api_key
            client = anthropic.Anthropic(api_key=key.get_secret_value() if key else None)
        self.client = client

    def _request(self, messages: list[dict[str, Any]], **extra: Any) -> Any:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.settings.ben_max_tokens,
            # Stable prefix (tools + system) is cached; volatile content lives in messages.
            "system": [
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": self.toolbox.definitions,
            "messages": messages,
        }
        if self.settings.ben_effort:
            params["output_config"] = {"effort": self.settings.ben_effort}
        params.update(extra)
        return self.client.messages.create(**params)

    def respond(self, history: list[dict[str, str]], user_text: str) -> AgentResult:
        """Answer `user_text` given prior text-only turns in `history`."""
        started = time.monotonic()
        messages: list[dict[str, Any]] = [*history, {"role": "user", "content": user_text}]
        usage = Usage()
        sources: list[SourceRef] = []
        tool_calls: list[dict[str, Any]] = []
        response = None

        try:
            for round_no in range(self.settings.ben_max_tool_rounds + 1):
                last_round = round_no == self.settings.ben_max_tool_rounds
                extra = {"tool_choice": {"type": "none"}} if last_round else {}
                response = self._request(messages, **extra)
                usage.add(response.usage)
                if response.stop_reason != "tool_use":
                    break
                # Keep the full assistant content (incl. thinking blocks) for the next round.
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in response.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    output = self.toolbox.execute(block.name, dict(block.input or {}))
                    sources.extend(output.sources)
                    tool_calls.append(
                        {"name": block.name, "input": block.input, "hits": len(output.sources)}
                    )
                    result: dict[str, Any] = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output.content,
                    }
                    if output.is_error:
                        result["is_error"] = True
                    results.append(result)
                # All results for one assistant turn go back in a single user message.
                messages.append({"role": "user", "content": results})
        except Exception:  # API, network or credential errors: reply politely, never crash
            log.exception("Claude API call failed")
            return AgentResult(
                text=ERROR_TEXT,
                sources=[],
                usage=usage,
                model=self.model,
                latency_ms=int((time.monotonic() - started) * 1000),
                tool_calls=tool_calls,
                stop_reason="error",
            )

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            text = REFUSAL_TEXT
        else:
            text = "\n\n".join(
                b.text for b in response.content if getattr(b, "type", None) == "text"
            ).strip()
            if stop_reason == "max_tokens":
                text += "\n\n_(Answer truncated - ask me to continue.)_"
            if not text:
                text = "Sorry - I couldn't produce an answer. Please try rephrasing."

        return AgentResult(
            text=text,
            sources=_dedupe(sources),
            usage=usage,
            model=self.model,
            latency_ms=int((time.monotonic() - started) * 1000),
            tool_calls=tool_calls,
            unverified_ids=self.unverified_control_ids(text),
            stop_reason=stop_reason,
        )

    def unverified_control_ids(self, text: str) -> list[str]:
        """Control-ID-shaped tokens in `text` that don't exist in the library."""
        known = self.toolbox.library.control_ids()
        if not known:
            return []
        mentioned = set(CONTROL_ID_PATTERN.findall(text))
        return sorted(mentioned - known)
