"""``jt docker`` — Docker housekeeping commands."""

from __future__ import annotations

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.services.docker import DockerService
from janitor.utils.console import console, err_console
from janitor.utils.format import format_bytes
from janitor.utils.prompt import confirm

app = typer.Typer(no_args_is_help=True, help="Docker disk and image housekeeping.")


def _service(ctx: typer.Context) -> DockerService | None:
    state: AppState = ctx.obj
    service = DockerService(runner=state.runner)
    if not service.is_available():
        err_console.print("[err]Docker is not available (CLI missing or daemon down).[/]")
        return None
    return service


@app.command()
def status(ctx: typer.Context) -> None:
    """Show ``docker system df`` usage and reclaimable space."""
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)
    usage = service.usage()
    table = Table(title="Docker Disk Usage", title_style="heading")
    table.add_column("Type")
    table.add_column("Total", justify="right")
    table.add_column("Active", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Reclaimable", justify="right", style="warn")
    for record in usage.records:
        table.add_row(
            record.type,
            str(record.total),
            str(record.active),
            format_bytes(record.size),
            format_bytes(record.reclaimable),
        )
    console.print(table)
    console.print(f"[accent]Total reclaimable:[/] {format_bytes(usage.total_reclaimable)}")


@app.command()
def reclaim(ctx: typer.Context) -> None:
    """Show how much space could be reclaimed (no changes made)."""
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)
    usage = service.usage()
    console.print(
        f"[accent]Reclaimable space:[/] {format_bytes(usage.total_reclaimable)} "
        f"of {format_bytes(usage.total_size)} used."
    )
    console.print("[muted]Run [bold]jt docker prune[/bold] to reclaim it.[/muted]")


@app.command()
def prune(
    ctx: typer.Context,
    aggressive: bool = typer.Option(
        False, "--aggressive", "-a", help="Remove all unused images, volumes, and cache."
    ),
) -> None:
    """Prune unused Docker data. Safe by default; ``--aggressive`` removes more."""
    state: AppState = ctx.obj
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)

    usage = service.usage()
    mode = "aggressive" if aggressive else "safe"
    console.print(
        f"[warn]About to run a [bold]{mode}[/bold] prune "
        f"(~{format_bytes(usage.total_reclaimable)} reclaimable).[/]"
    )
    if state.dry_run:
        console.print("[muted]Dry-run: no changes will be made.[/muted]")
    elif not confirm("Proceed with prune?", assume_yes=state.assume_yes):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)

    ran = service.prune(
        all_images=aggressive,
        volumes=aggressive or state.config.docker.prune_volumes,
        build_cache=state.config.docker.prune_build_cache,
    )
    for description in ran:
        prefix = "[muted]would run[/muted]" if state.dry_run else "[ok]ran[/ok]"
        console.print(f"{prefix}: {description}")
    console.print("[ok]Prune complete.[/]")


@app.command()
def images(ctx: typer.Context) -> None:
    """List Docker images, flagging dangling ones."""
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)
    table = Table(title="Docker Images", title_style="heading")
    table.add_column("Repository")
    table.add_column("Tag")
    table.add_column("Size", justify="right")
    table.add_column("Dangling", justify="center")
    for image in sorted(service.images(), key=lambda i: i.size, reverse=True):
        table.add_row(
            image.repository,
            image.tag,
            format_bytes(image.size),
            "[warn]yes[/]" if image.dangling else "",
        )
    console.print(table)


@app.command()
def volumes(ctx: typer.Context) -> None:
    """List Docker volumes, flagging unused ones."""
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)
    table = Table(title="Docker Volumes", title_style="heading")
    table.add_column("Name")
    table.add_column("Driver")
    table.add_column("In use", justify="center")
    for volume in service.volumes():
        table.add_row(
            volume.name,
            volume.driver,
            "[ok]yes[/]" if volume.in_use else "[warn]no[/]",
        )
    console.print(table)
