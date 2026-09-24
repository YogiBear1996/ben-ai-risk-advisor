"""Wiring: builds the library, agent and service from settings."""

from __future__ import annotations

import logging

from ben.config import Settings
from ben.core.agent import BenAgent
from ben.core.prompts import load_system_prompt
from ben.core.tools import ToolBox
from ben.knowledge.embeddings import build_embedder
from ben.knowledge.library import KnowledgeLibrary
from ben.knowledge.store import VectorStore
from ben.knowledge.types import EmptyLibrary, Library


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, which include the Telegram bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_store(settings: Settings) -> VectorStore:
    return VectorStore(settings.resolved_vector_dir, build_embedder(settings))


def build_library(settings: Settings) -> Library:
    if not settings.resolved_vector_dir.exists():
        logging.getLogger(__name__).warning("No index found - run `ben ingest` first.")
        return EmptyLibrary()
    return KnowledgeLibrary(settings, build_store(settings))


def build_agent(settings: Settings, library: Library | None = None, client=None) -> BenAgent:
    library = library or build_library(settings)
    toolbox = ToolBox(library, top_k=settings.retrieval_top_k)
    return BenAgent(settings, toolbox, load_system_prompt(settings.prompts_dir), client=client)
