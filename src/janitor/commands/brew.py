"""``jt brew`` — Homebrew maintenance."""

from __future__ import annotations

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.services.brew import BrewService
from janitor.utils.console import console, err_console
from janitor.utils.prompt import confirm

app = typer.Typer(no_args_is_help=True, help="Homebrew package maintenance.")


def _service(ctx: typer.Context) -> BrewService | None:
    state: AppState = ctx.obj
    service = BrewService(runner=state.runner)
    if not service.is_available():
        err_console.print("[err]Homebrew is not installed.[/]")
        return None
    return service


@app.command()
def status(ctx: typer.Context) -> None:
    """Show Homebrew prefix and outdated packages."""
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)
    info = service.status()
    console.print(f"[info]Prefix:[/] {info.prefix or 'unknown'}")
    if not info.outdated:
        console.print("[ok]Everything is up to date.[/]")
        return
    table = Table(title=f"Outdated ({info.outdated_count})", title_style="heading")
    table.add_column("Package")
    table.add_column("Installed", style="muted")
    table.add_column("Latest", style="ok")
    table.add_column("Type", justify="center")
    for item in info.outdated:
        table.add_row(
            item.name,
            item.current_version,
            item.latest_version,
            "cask" if item.is_cask else "formula",
        )
    console.print(table)


@app.command()
def upgrade(ctx: typer.Context) -> None:
    """Upgrade all outdated Homebrew packages."""
    state: AppState = ctx.obj
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)
    info = service.status()
    if not info.outdated:
        console.print("[ok]Nothing to upgrade.[/]")
        return
    console.print(f"[warn]{info.outdated_count} package(s) will be upgraded.[/]")
    if not state.dry_run and not confirm("Proceed?", assume_yes=state.assume_yes):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)
    with console.status("[info]Upgrading packages...", spinner="dots"):
        output = service.upgrade()
    console.print(output or "[ok]Upgrade complete.[/]")


@app.command()
def cleanup(ctx: typer.Context) -> None:
    """Remove old versions and clear Homebrew's download cache."""
    state: AppState = ctx.obj
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)
    if not state.dry_run and not confirm(
        "Remove old versions and caches?", assume_yes=state.assume_yes
    ):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)
    with console.status("[info]Cleaning up...", spinner="dots"):
        output = service.cleanup()
    console.print(output or "[ok]Cleanup complete.[/]")
