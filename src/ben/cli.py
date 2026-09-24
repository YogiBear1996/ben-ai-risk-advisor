"""`ben` command-line interface."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown

from ben.config import get_settings
from ben.core.models import Channel, IncomingMessage
from ben.knowledge.ingest import ingest as run_ingest
from ben.runtime import build_library, build_service, build_store, configure_logging

app = typer.Typer(help="Ben - AI governance assistant.", no_args_is_help=True)
console = Console()


@app.command()
def chat(user: str = typer.Option("local", help="User ID recorded for this session.")) -> None:
    """Chat with Ben in the terminal - same pipeline as Telegram/WhatsApp, no channel needed."""
    settings = get_settings()
    configure_logging(settings)
    service = build_service(settings)
    console.print(f"[bold]Ben[/bold] ({settings.ben_model}). Commands: /help /reset /sources /quit")
    while True:
        try:
            text = console.input("[bold cyan]you>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            break
        msg = IncomingMessage(channel=Channel.CLI, chat_id=user, user_id=user, text=text)
        with console.status("Ben is thinking..."):
            reply = asyncio.run(service.handle(msg))
        if reply:
            console.print(Markdown(reply))


@app.command()
def ingest(
    path: Path = typer.Argument(None, help="Knowledge folder (default: KNOWLEDGE_DIR)."),
    force: bool = typer.Option(False, "--force", help="Re-process every file."),
) -> None:
    """Ingest PDF/DOCX/Markdown/XLSX files. Re-runs only process changed files."""
    settings = get_settings()
    configure_logging(settings)
    report = run_ingest(path or settings.knowledge_dir, settings, build_store(settings), force)
    console.print(
        f"Added {len(report.added)}, updated {len(report.updated)}, "
        f"removed {len(report.removed)}, unchanged {len(report.unchanged)} "
        f"({report.chunks} chunks written)."
    )
    for rel, err in report.failed.items():
        console.print(f"[red]Failed[/red] {rel}: {err}")
    if report.failed:
        raise typer.Exit(1)


@app.command()
def frameworks() -> None:
    """List the documents and frameworks currently in the library."""
    settings = get_settings()
    items = build_library(settings).list_frameworks()
    if not items:
        console.print("Library is empty - run `ben ingest` first.")
    for f in items:
        console.print(f"- {f.title} [dim]({f.source_path}, {f.doc_type}, {f.chunks} chunks)[/dim]")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address."),  # noqa: S104 - container default
    port: int = typer.Option(8000, help="Port."),
) -> None:
    """Run the webhook server (FastAPI + uvicorn)."""
    import uvicorn

    from ben.web.app import create_app

    uvicorn.run(create_app(), host=host, port=port, proxy_headers=True, log_config=None)


@app.command("telegram-poll")
def telegram_poll() -> None:
    """Run the Telegram bot with long polling (local development - no public URL needed)."""
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters

    from ben.channels.telegram import TelegramAdapter
    from ben.web.app import AppState, process_message

    settings = get_settings()
    configure_logging(settings)
    if not settings.telegram_bot_token:
        raise typer.BadParameter("TELEGRAM_BOT_TOKEN is not set")
    application = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .concurrent_updates(True)
        .build()
    )
    adapter = TelegramAdapter(application.bot)
    state = AppState(settings, build_service(settings), {"telegram": adapter})

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        for msg in adapter.parse_incoming(update.to_dict()):
            await process_message(state, adapter, msg)

    application.add_handler(MessageHandler(filters.ALL, on_message))
    console.print("Polling Telegram... (Ctrl+C to stop)")
    application.run_polling(allowed_updates=["message"])


@app.command("set-webhook")
def set_webhook(
    delete: bool = typer.Option(False, "--delete", help="Remove the webhook instead."),
) -> None:
    """Register (or remove) the Telegram webhook at TELEGRAM_WEBHOOK_URL/webhooks/telegram."""
    from telegram import Bot

    settings = get_settings()
    if not settings.telegram_bot_token:
        raise typer.BadParameter("TELEGRAM_BOT_TOKEN is not set")
    bot = Bot(settings.telegram_bot_token.get_secret_value())

    async def run() -> None:
        async with bot:
            if delete:
                await bot.delete_webhook()
                console.print("Webhook removed.")
                return
            if not settings.telegram_webhook_url or not settings.telegram_webhook_secret:
                raise typer.BadParameter("Set TELEGRAM_WEBHOOK_URL and TELEGRAM_WEBHOOK_SECRET")
            url = settings.telegram_webhook_url.rstrip("/") + "/webhooks/telegram"
            await bot.set_webhook(
                url=url,
                secret_token=settings.telegram_webhook_secret.get_secret_value(),
                allowed_updates=["message"],
            )
            console.print(f"Webhook set to {url}")

    asyncio.run(run())


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@app.command("audit-export")
def audit_export(
    out: Path = typer.Option(Path("data/audit_export.csv"), "--out", help="CSV file to write."),
    since: str = typer.Option(None, help="Start date/time (ISO, UTC), e.g. 2026-01-01."),
    until: str = typer.Option(None, help="End date/time (ISO, UTC), exclusive."),
) -> None:
    """Export the audit log to CSV."""
    from ben.storage.repo import AuditRepo

    count = AuditRepo(get_settings()).export_csv(out, _parse_date(since), _parse_date(until))
    console.print(f"Exported {count} audit entries to {out}")


@app.command()
def cleanup() -> None:
    """Apply data retention now (also runs daily inside `ben serve`)."""
    from ben.storage.repo import run_retention

    removed = run_retention(get_settings())
    console.print(
        f"Removed {removed['conversations']} conversations and "
        f"{removed['audit_entries']} audit entries."
    )


@app.command("eval")
def eval_(
    questions: Path = typer.Option(Path("evals/questions.yaml"), help="Eval set (YAML)."),
    retrieval_only: bool = typer.Option(
        False, "--retrieval-only", help="Only check retrieval; no model calls."
    ),
    judge: bool = typer.Option(False, "--judge", help="Also grade groundedness with Claude."),
    only: list[str] = typer.Option(None, "--only", help="Run only these question IDs."),
    out: Path = typer.Option(None, help="JSON report path (default: reports/eval-<time>.json)."),
) -> None:
    """Run the eval set and report citation accuracy and groundedness."""
    from rich.table import Table

    from ben.evals import runner

    settings = get_settings()
    configure_logging(settings)
    cases = runner.load_cases(questions)
    if only:
        cases = [c for c in cases if c.id in set(only)]
    library = build_library(settings)
    if not library.list_frameworks():
        console.print("[red]Library is empty - run `ben ingest` first.[/red]")
        raise typer.Exit(1)

    if not retrieval_only and settings.anthropic_api_key is None:
        console.print("[yellow]ANTHROPIC_API_KEY not set - running retrieval-only.[/yellow]")
        retrieval_only = True
    mode = "retrieval" if retrieval_only else "full"

    def progress(r: runner.CaseResult) -> None:
        mark = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        console.print(
            f"{mark} {r.id}" + (f"  [dim]{'; '.join(r.missing)}[/dim]" if r.missing else "")
        )

    if retrieval_only:
        results = runner.run_retrieval_only(cases, library, settings.retrieval_top_k, progress)
        model = None
    else:
        from ben.runtime import build_agent

        agent = build_agent(settings, library=library)
        results = runner.run_full(cases, agent, judge=judge, on_result=progress)
        model = settings.ben_model

    summary = runner.summarise(results, mode, model)
    table = Table(title=f"Ben eval ({mode})")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key, value in summary.items():
        table.add_row(key, str(value))
    console.print(table)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = out or Path("reports") / f"eval-{mode}-{stamp}.json"
    runner.write_report(report, summary, results)
    console.print(f"Report written to {report}")


if __name__ == "__main__":
    app()
