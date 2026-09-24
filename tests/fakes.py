"""Test doubles for the Anthropic client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "toolu_1"
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list[Any]
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)


def text_response(text: str) -> FakeResponse:
    return FakeResponse([TextBlock(text)])


def tool_response(name: str, tool_input: dict[str, Any], tool_id: str = "toolu_1") -> FakeResponse:
    return FakeResponse([ToolUseBlock(name, tool_input, tool_id)], stop_reason="tool_use")


class FakeMessages:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **params: Any) -> FakeResponse:
        # Snapshot messages - the agent mutates the list between rounds.
        self.calls.append({**params, "messages": list(params["messages"])})
        if not self.responses:
            raise AssertionError("FakeAnthropic ran out of scripted responses")
        return self.responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.messages = FakeMessages(responses)
