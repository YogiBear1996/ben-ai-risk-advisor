"""`ben` command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown

from ben.config import get_settings
from ben.knowledge.ingest import ingest as run_ingest
from ben.runtime import build_agent, build_library, build_store, configure_logging

app = typer.Typer(help="Ben - AI governance assistant.", no_args_is_help=True)
console = Console()


@app.command()
def chat() -> None:
    """Chat with Ben in the terminal (no channel needed)."""
    settings = get_settings()
    configure_logging(settings)
    agent = build_agent(settings)
    history: list[dict[str, str]] = []
    console.print(f"[bold]Ben[/bold] ({settings.ben_model}). Type /reset or /quit.")
    while True:
        try:
            text = console.input("[bold cyan]you>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            break
        if text == "/reset":
            history.clear()
            console.print("[dim]Conversation reset.[/dim]")
            continue
        result = agent.respond(history[-settings.ben_history_turns * 2 :], text)
        history += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": result.text},
        ]
        console.print(Markdown(result.text))
        if result.sources:
            console.print(
                "[dim]Sources: " + "; ".join(s.label() for s in result.sources) + "[/dim]"
            )
        console.print(
            f"[dim]{result.latency_ms} ms, {result.usage.input_tokens} in / "
            f"{result.usage.output_tokens} out tokens[/dim]"
        )


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


if __name__ == "__main__":
    app()
