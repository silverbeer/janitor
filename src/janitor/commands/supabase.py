"""``jt supabase`` — local Supabase project management."""

from __future__ import annotations

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.services.supabase import SupabaseService
from janitor.utils.console import console, err_console
from janitor.utils.prompt import confirm

app = typer.Typer(no_args_is_help=True, help="Local Supabase project housekeeping.")


@app.command(name="list")
def list_projects(ctx: typer.Context) -> None:
    """Discover local Supabase projects and show their status."""
    state: AppState = ctx.obj
    service = SupabaseService(runner=state.runner)
    if not service.cli_available():
        console.print("[warn]Supabase CLI not installed — discovery only.[/]")
    with console.status("[info]Discovering Supabase projects...", spinner="dots"):
        projects = service.discover(state.config.supabase.search_paths)
        projects = [service.status(p) for p in projects]
    if not projects:
        console.print("[muted]No Supabase projects found in configured search paths.[/muted]")
        return
    table = Table(title="Supabase Projects", title_style="heading", expand=True)
    table.add_column("Name")
    table.add_column("Status", justify="center")
    table.add_column("Path", style="muted")
    for project in projects:
        status = "[ok]running[/]" if project.running else "[muted]stopped[/]"
        table.add_row(project.name, status, str(project.path))
    console.print(table)


@app.command()
def backup(
    ctx: typer.Context,
    name: str = typer.Argument(None, help="Project name to back up (defaults to all)."),
) -> None:
    """Create a timestamped database dump for one or all projects."""
    state: AppState = ctx.obj
    service = SupabaseService(runner=state.runner)
    if not service.cli_available():
        err_console.print("[err]Supabase CLI is required for backups.[/]")
        raise typer.Exit(code=1)
    projects = service.discover(state.config.supabase.search_paths)
    if name:
        projects = [p for p in projects if p.name == name]
    if not projects:
        err_console.print("[err]No matching Supabase projects found.[/]")
        raise typer.Exit(code=1)
    if not state.dry_run and not confirm(
        f"Back up {len(projects)} project(s)?", assume_yes=state.assume_yes
    ):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)
    for project in projects:
        with console.status(f"[info]Backing up {project.name}...", spinner="dots"):
            destination = service.backup(project, state.config.supabase.backup_dir)
        verb = "Would write" if state.dry_run else "Wrote"
        console.print(f"[ok]{verb} backup:[/] {destination}")
