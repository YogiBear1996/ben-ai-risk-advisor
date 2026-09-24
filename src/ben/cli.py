"""`ben` command-line interface."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.markdown import Markdown

from ben.config import get_settings
from ben.runtime import build_agent, configure_logging

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


if __name__ == "__main__":
    app()
