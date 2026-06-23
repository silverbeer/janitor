"""``jt disk`` — disk usage and large file discovery."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.services.disk import DiskService
from janitor.utils.console import console
from janitor.utils.format import format_bytes

app = typer.Typer(no_args_is_help=True, help="Disk usage analysis and reclamation.")


@app.command()
def usage(
    ctx: typer.Context,
    path: Path = typer.Argument(Path("/"), help="Filesystem path to inspect."),
) -> None:
    """Show filesystem utilization for a mount point."""
    service = DiskService()
    info = service.usage(path)
    bar_style = "err" if info.percent_used >= 90 else "warn" if info.percent_used >= 75 else "ok"
    table = Table(title=f"Disk Usage — {info.path}", title_style="heading")
    table.add_column("Total", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Free", justify="right")
    table.add_column("Used %", justify="right")
    table.add_row(
        format_bytes(info.total),
        format_bytes(info.used),
        format_bytes(info.free),
        f"[{bar_style}]{info.percent_used}%[/]",
    )
    console.print(table)


@app.command("largest-files")
def largest_files(
    ctx: typer.Context,
    path: list[Path] = typer.Argument(None, help="Roots to scan (defaults to config)."),
    top: int = typer.Option(20, "--top", "-n", help="Number of entries to show."),
) -> None:
    """List the largest files under the given roots."""
    state: AppState = ctx.obj
    roots = list(path) if path else state.config.disk.scan_paths
    service = DiskService()
    min_size = state.config.disk.min_size_mb * 1024 * 1024
    with console.status("[info]Scanning for large files...", spinner="dots"):
        entries = service.largest_files(roots, top_n=top, min_size=min_size)
    _render_entries("Largest Files", entries)


@app.command("largest-dirs")
def largest_dirs(
    ctx: typer.Context,
    path: list[Path] = typer.Argument(None, help="Roots to scan (defaults to config)."),
    top: int = typer.Option(20, "--top", "-n", help="Number of entries to show."),
) -> None:
    """List the largest immediate subdirectories under the given roots."""
    state: AppState = ctx.obj
    roots = list(path) if path else state.config.disk.scan_paths
    service = DiskService()
    min_size = state.config.disk.min_size_mb * 1024 * 1024
    with console.status("[info]Measuring directory sizes...", spinner="dots"):
        entries = service.largest_dirs(roots, top_n=top, min_size=min_size)
    _render_entries("Largest Directories", entries)


@app.command()
def reclaim(
    ctx: typer.Context,
    path: list[Path] = typer.Argument(None, help="Roots to scan (defaults to config)."),
) -> None:
    """Highlight common space offenders (caches, node_modules, build artifacts)."""
    state: AppState = ctx.obj
    roots = list(path) if path else state.config.disk.scan_paths
    service = DiskService()
    min_size = state.config.disk.min_size_mb * 1024 * 1024
    with console.status("[info]Hunting for reclaimable directories...", spinner="dots"):
        entries = service.largest_dirs(roots, top_n=100, min_size=min_size)
    offenders = [e for e in entries if e.category]
    if not offenders:
        console.print("[ok]No common offenders found above the size threshold.[/]")
        return
    total = sum(e.size for e in offenders)
    _render_entries("Reclaimable Offenders", offenders[:25])
    console.print(f"[accent]Total in flagged directories:[/] {format_bytes(total)}")
    console.print(
        "[muted]Review carefully before deleting — Janitor will not auto-remove these.[/muted]"
    )


def _render_entries(title: str, entries: list) -> None:  # type: ignore[type-arg]
    table = Table(title=title, title_style="heading", expand=True)
    table.add_column("Size", justify="right", style="accent")
    table.add_column("Category", style="warn")
    table.add_column("Path")
    if not entries:
        console.print(f"[muted]No entries found for {title.lower()}.[/muted]")
        return
    for entry in entries:
        table.add_row(
            format_bytes(entry.size),
            entry.category or "",
            str(entry.path),
        )
    console.print(table)
