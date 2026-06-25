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


@app.command(name="restore-from-prod")
def restore_from_prod(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Project name to restore into the local DB."),
) -> None:
    """Reset the local DB to migrations, then load prod data into it.

    Destructive: wipes the local database. Requires the project's
    ``prod_db_url_env`` to be configured and that env var set.
    """
    state: AppState = ctx.obj
    service = SupabaseService(runner=state.runner)
    sb = state.config.supabase

    if not service.cli_available():
        err_console.print("[err]Supabase CLI is required (for db reset).[/]")
        raise typer.Exit(code=1)
    if not service.pg_client_available():
        err_console.print(
            "[err]pg_dump / psql not found.[/] Install the Postgres client:\n"
            "  [bold]brew install libpq && brew link --force libpq[/]"
        )
        raise typer.Exit(code=1)

    project = next(
        (p for p in service.discover(sb.search_paths) if p.name == name),
        None,
    )
    if project is None:
        err_console.print(f"[err]Project '{name}' not found in search paths.[/]")
        raise typer.Exit(code=1)

    prod_db_url = sb.resolved_prod_db_url(name)
    if not prod_db_url:
        env_name = sb.project(name).prod_db_url_env
        hint = (
            f"set ${env_name}"
            if env_name
            else f"configure supabase.projects.{name}.prod_db_url_env"
        )
        err_console.print(f"[err]No prod DB URL for '{name}'[/] — {hint}.")
        raise typer.Exit(code=1)

    project_cfg = sb.project(name)
    local_db_url = project_cfg.local_db_url
    console.print(
        f"[warn]This RESETS the local DB[/] for [bold]{name}[/] "
        f"({local_db_url}) and loads prod data into it."
    )
    if not state.dry_run and not confirm(
        "Continue? Local data will be destroyed.", assume_yes=state.assume_yes
    ):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)

    try:
        with console.status(f"[info]Restoring {name} from prod...", spinner="dots"):
            result = service.restore_from_prod(
                name,
                project.path,
                local_db_url=local_db_url,
                prod_db_url=prod_db_url,
                data_schemas=project_cfg.data_schemas,
            )
    except ValueError as exc:  # local-target safety guard tripped
        err_console.print(f"[err]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if result.dry_run:
        console.print(f"[muted]Dry-run: would reset + load prod data for {name}.[/muted]")
    else:
        console.print(
            f"[ok]Restored {name}[/] — reset local, loaded "
            f"{format_bytes(result.dumped_bytes)} of prod data."
        )
