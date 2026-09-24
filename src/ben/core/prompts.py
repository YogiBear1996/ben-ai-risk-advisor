"""Loads Ben's system prompt from `prompts/system.md`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache
def load_system_prompt(prompts_dir: Path) -> str:
    return (prompts_dir / "system.md").read_text(encoding="utf-8").strip()
