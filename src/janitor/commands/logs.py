"""``jt logs`` — log file discovery and cleanup."""

from __future__ import annotations

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.services.logs import LogsService
from janitor.utils.console import console
from janitor.utils.format import format_bytes
from janitor.utils.prompt import confirm

app = typer.Typer(no_args_is_help=True, help="Log file discovery and rotation.")


@app.command()
def size(ctx: typer.Context) -> None:
    """List large log files under the configured paths."""
    state: AppState = ctx.obj
    service = LogsService(dry_run=state.dry_run)
    min_size = state.config.logs.min_size_mb * 1024 * 1024
    with console.status("[info]Scanning for log files...", spinner="dots"):
        logs = service.find(state.config.logs.paths, min_size=min_size)
    if not logs:
        console.print("[ok]No log files above the size threshold.[/]")
        return
    table = Table(title="Large Log Files", title_style="heading", expand=True)
    table.add_column("Size", justify="right", style="accent")
    table.add_column("Age (days)", justify="right")
    table.add_column("Path")
    for log in logs:
        table.add_row(format_bytes(log.size), str(log.age_days), str(log.path))
    console.print(table)
    console.print(f"[accent]Total:[/] {format_bytes(sum(log.size for log in logs))}")


@app.command()
def clean(
    ctx: typer.Context,
    max_age: int = typer.Option(
        None, "--max-age", help="Delete logs older than N days (defaults to config)."
    ),
) -> None:
    """Delete log files older than the age threshold."""
    state: AppState = ctx.obj
    service = LogsService(dry_run=state.dry_run)
    threshold = max_age if max_age is not None else state.config.logs.max_age_days
    with console.status("[info]Finding stale logs...", spinner="dots"):
        logs = service.find(state.config.logs.paths)
    candidates = [log for log in logs if log.age_days >= threshold]
    if not candidates:
        console.print(f"[ok]No logs older than {threshold} days.[/]")
        return
    reclaim = format_bytes(sum(log.size for log in candidates))
    console.print(
        f"[warn]{len(candidates)} log(s) older than {threshold} days "
        f"({reclaim}) will be removed.[/]"
    )
    if state.dry_run:
        console.print("[muted]Dry-run: no files will be deleted.[/muted]")
    elif not confirm("Delete these logs?", assume_yes=state.assume_yes):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)
    removed = service.clean(candidates, max_age_days=threshold)
    verb = "Would remove" if state.dry_run else "Removed"
    console.print(f"[ok]{verb} {len(removed)} log file(s).[/]")
