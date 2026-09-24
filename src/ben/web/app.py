"""FastAPI app: health endpoint and channel webhooks."""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from ben.channels.base import ChannelAdapter
from ben.config import Settings, get_settings
from ben.core.models import IncomingMessage
from ben.core.service import BenService
from ben.storage.repo import run_retention

log = logging.getLogger(__name__)


@dataclass
class AppState:
    settings: Settings
    service: BenService
    adapters: dict[str, ChannelAdapter] = field(default_factory=dict)


def _secret_matches(expected, provided: str | None) -> bool:
    return bool(expected and provided) and hmac.compare_digest(
        expected.get_secret_value().encode(), provided.encode()
    )


async def process_message(state: AppState, adapter: ChannelAdapter, msg: IncomingMessage) -> None:
    try:
        await adapter.send_typing(msg.chat_id)
        reply = await state.service.handle(msg)
        if reply:
            await adapter.send_reply(msg.chat_id, reply)
    except Exception:
        log.exception("Failed to process %s message", msg.channel)


def start_retention_scheduler(settings: Settings) -> AsyncIOScheduler:
    """Daily cleanup of old conversations and audit entries."""

    def job() -> None:
        removed = run_retention(settings)
        log.info("Retention cleanup removed %s", removed)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(job, "cron", hour=settings.cleanup_hour_utc, minute=0, id="retention")
    scheduler.start()
    return scheduler


def create_app(
    settings: Settings | None = None,
    service: BenService | None = None,
    adapters: dict[str, ChannelAdapter] | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from ben.runtime import build_service, build_telegram_adapter, configure_logging

        configure_logging(settings)
        state = AppState(settings, service or build_service(settings), dict(adapters or {}))
        if "telegram" not in state.adapters and settings.telegram_bot_token:
            state.adapters["telegram"] = await build_telegram_adapter(settings)
        if not settings.telegram_allowed_user_ids and not settings.whatsapp_allowed_numbers:
            log.warning("Allowlists are empty - every Telegram/WhatsApp user will be refused")
        scheduler = start_retention_scheduler(settings)
        app.state.ben = state
        yield
        scheduler.shutdown(wait=False)
        tg = state.adapters.get("telegram")
        if tg is not None and hasattr(tg, "bot") and hasattr(tg.bot, "shutdown"):
            await tg.bot.shutdown()

    app = FastAPI(title="Ben", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health(request: Request) -> dict:
        state: AppState = request.app.state.ben
        return {
            "status": "ok",
            "model": state.settings.ben_model,
            "channels": sorted(state.adapters),
            "documents": len(state.service.library.list_frameworks()),
        }

    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request, background: BackgroundTasks) -> dict:
        state: AppState = request.app.state.ben
        adapter = state.adapters.get("telegram")
        if adapter is None:
            raise HTTPException(404, "Telegram is not configured")
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not _secret_matches(state.settings.telegram_webhook_secret, token):
            log.warning("Rejected Telegram webhook with invalid secret token")
            raise HTTPException(401, "Invalid secret token")
        payload = await request.json()
        for msg in adapter.parse_incoming(payload):
            # Reply after returning 200 so Telegram doesn't retry slow answers.
            background.add_task(process_message, state, adapter, msg)
        return {"ok": True}

    return app
