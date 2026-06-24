"""``jt supabase`` — local Supabase project management."""

from __future__ import annotations

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.services.supabase import SupabaseService
from janitor.utils.console import console, err_console
from janitor.utils.format import format_age, format_bytes
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
    sb = state.config.supabase
    for project in projects:
        backup_dir = sb.resolved_backup_dir(project.name)
        retention_count, retention_days, _ = sb.resolved_retention(project.name)
        with console.status(f"[info]Backing up {project.name}...", spinner="dots"):
            destination = service.backup(project, backup_dir)
            pruned = service.prune_backups(
                project.name,
                backup_dir,
                retention_count=retention_count,
                retention_days=retention_days,
            )
        verb = "Would write" if state.dry_run else "Wrote"
        console.print(f"[ok]{verb} backup:[/] {destination}")
        if pruned:
            pverb = "would prune" if state.dry_run else "pruned"
            console.print(f"[muted]Retention: {pverb} {len(pruned)} old backup(s).[/muted]")


@app.command()
def backups(
    ctx: typer.Context,
    name: str = typer.Argument(None, help="Project name (defaults to all discovered)."),
) -> None:
    """List backups per project with sizes and flag retention/size breaches."""
    state: AppState = ctx.obj
    service = SupabaseService(runner=state.runner)
    sb = state.config.supabase
    projects = service.discover(sb.search_paths)
    if name:
        projects = [p for p in projects if p.name == name]
    if not projects:
        console.print("[muted]No matching Supabase projects found.[/muted]")
        return

    any_issue = False
    for project in projects:
        backup_dir = sb.resolved_backup_dir(project.name)
        retention_count, retention_days, max_dir_size_mb = sb.resolved_retention(project.name)
        report = service.report(
            project.name,
            backup_dir,
            retention_count=retention_count,
            retention_days=retention_days,
            max_dir_size_mb=max_dir_size_mb,
        )
        size_style = "err" if report.over_size else "ok"
        ceiling = format_bytes(report.max_size) if report.max_size else "unlimited"
        table = Table(
            title=f"{project.name} — {report.count} backup(s), "
            f"[{size_style}]{format_bytes(report.total_size)}[/] / {ceiling}",
            title_style="heading",
            caption=str(report.directory),
            caption_style="muted",
            expand=True,
        )
        table.add_column("Backup")
        table.add_column("Size", justify="right")
        table.add_column("Age", justify="right", style="muted")
        table.add_column("", justify="center")
        prunable_paths = {f.path for f in report.prunable}
        for file in report.files:
            flag = "[muted]prune[/]" if file.path in prunable_paths else ""
            table.add_row(
                file.path.name,
                format_bytes(file.size),
                format_age(file.mtime),
                flag,
            )
        console.print(table)
        if report.over_size:
            any_issue = True
            console.print(
                f"[err]⚠ {project.name} backup dir over size ceiling[/] "
                f"({format_bytes(report.total_size)} > {ceiling}) — run "
                f"[bold]jt supabase backup {project.name}[/] or raise retention."
            )
        if report.prunable:
            any_issue = True
            console.print(
                f"[warn]{len(report.prunable)} backup(s) exceed retention[/] — "
                f"the next [bold]backup[/] prunes them, or run backup now."
            )
    if not any_issue:
        console.print("[ok]All backup dirs within limits.[/]")
