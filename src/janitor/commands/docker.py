"""``jt docker`` — Docker housekeeping commands."""

from __future__ import annotations

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.models.docker import DockerImage
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
    state: AppState = ctx.obj
    usage = service.usage()
    build_cache = state.config.docker.prune_build_cache
    safe = service.reclaimable_estimate(
        usage,
        all_images=False,
        volumes=state.config.docker.prune_volumes,
        build_cache=build_cache,
    )
    aggressive = service.reclaimable_estimate(
        usage, all_images=True, volumes=True, build_cache=build_cache
    )
    console.print(
        f"[accent]Reclaimable space:[/] {format_bytes(usage.total_reclaimable)} "
        f"of {format_bytes(usage.total_size)} used."
    )
    console.print(f"  [muted]jt docker prune[/muted]              ~{format_bytes(safe)}")
    console.print(f"  [muted]jt docker prune --aggressive[/muted] ~{format_bytes(aggressive)}")
    console.print(
        "[muted]Aggressive also removes unused-but-tagged images and unused volumes.[/muted]"
    )


@app.command()
def prune(
    ctx: typer.Context,
    aggressive: bool = typer.Option(
        False, "--aggressive", "-a", help="Remove all unused images, volumes, and cache."
    ),
    stale: bool = typer.Option(
        False,
        "--stale",
        help="Remove only images superseded by a newer tag of the same repository.",
    ),
) -> None:
    """Prune unused Docker data. Safe by default; ``--aggressive`` removes more."""
    state: AppState = ctx.obj
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)

    if stale and aggressive:
        err_console.print("[err]--stale and --aggressive are mutually exclusive.[/]")
        raise typer.Exit(code=2)
    if stale:
        _prune_stale(state, service)
        return

    usage = service.usage()
    prune_volumes = aggressive or state.config.docker.prune_volumes
    prune_build_cache = state.config.docker.prune_build_cache
    estimate = service.reclaimable_estimate(
        usage,
        all_images=aggressive,
        volumes=prune_volumes,
        build_cache=prune_build_cache,
    )
    mode = "an [bold]aggressive[/bold]" if aggressive else "a [bold]safe[/bold]"
    console.print(f"[warn]About to run {mode} prune (~{format_bytes(estimate)} reclaimable).[/]")
    if not aggressive and estimate < usage.total_reclaimable:
        console.print(
            f"[muted]Docker reports {format_bytes(usage.total_reclaimable)} reclaimable in "
            f"total; the rest is unused-but-tagged images and volumes that only "
            f"[bold]--aggressive[/bold] removes.[/muted]"
        )
    if state.dry_run:
        console.print("[muted]Dry-run: no changes will be made.[/muted]")
    elif not confirm("Proceed with prune?", assume_yes=state.assume_yes):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)

    ran = service.prune(
        all_images=aggressive,
        volumes=prune_volumes,
        build_cache=prune_build_cache,
    )
    for description in ran:
        prefix = "[muted]would run[/muted]" if state.dry_run else "[ok]ran[/ok]"
        console.print(f"{prefix}: {description}")
    console.print("[ok]Prune complete.[/]")


def _prune_stale(state: AppState, service: DockerService) -> None:
    """Remove superseded image versions, keeping everything in use."""
    stale_images = service.stale_images()
    if not stale_images:
        console.print("[ok]No stale images — nothing to prune.[/]")
        return

    total = sum(image.size for image in stale_images)
    console.print(
        f"[warn]About to remove [bold]{len(stale_images)}[/bold] stale image(s) "
        f"(up to {format_bytes(total)}).[/]"
    )
    console.print(
        "[muted]Images in use by any container — running or stopped — are kept, as is "
        "the newest version of every repository.[/muted]"
    )
    if state.dry_run:
        console.print("[muted]Dry-run: no changes will be made.[/muted]")
    elif not confirm("Proceed with stale image removal?", assume_yes=state.assume_yes):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)

    removed = service.remove_images([image.reference for image in stale_images])
    verb = "would remove" if state.dry_run else "removed"
    console.print(f"[ok]{verb.capitalize()} {len(removed)} of {len(stale_images)} image(s).[/]")
    if not state.dry_run and len(removed) < len(stale_images):
        console.print(
            "[muted]Some images were skipped — they are still referenced by another tag "
            "or image.[/muted]"
        )


@app.command()
def images(
    ctx: typer.Context,
    stale: bool = typer.Option(
        False, "--stale", help="Only images superseded by a newer tag of the same repository."
    ),
) -> None:
    """List Docker images, flagging dangling and stale ones."""
    service = _service(ctx)
    if service is None:
        raise typer.Exit(code=1)
    listed = service.images()
    if stale:
        listed = [image for image in listed if image.stale]
        if not listed:
            console.print("[ok]No stale images — every repository is on a single version.[/]")
            return
    table = Table(title="Stale Docker Images" if stale else "Docker Images", title_style="heading")
    table.add_column("Repository")
    table.add_column("Tag")
    table.add_column("Size", justify="right")
    table.add_column("Dangling", justify="center")
    table.add_column("Stale", justify="center")
    for image in sorted(listed, key=lambda i: i.size, reverse=True):
        table.add_row(
            image.repository,
            image.tag,
            format_bytes(image.size),
            "[warn]yes[/]" if image.dangling else "",
            "[warn]yes[/]" if image.stale else "",
        )
    console.print(table)
    if stale:
        _print_stale_total(listed)


def _print_stale_total(stale_images: list[DockerImage]) -> None:
    total = sum(image.size for image in stale_images)
    console.print(
        f"[accent]{len(stale_images)} stale image(s):[/] up to {format_bytes(total)} reclaimable."
    )
    console.print(
        "[muted]Actual reclaim is lower — versions of the same image share base layers.[/muted]"
    )


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
