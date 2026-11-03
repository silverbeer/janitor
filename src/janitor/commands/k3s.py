"""``jt k3s`` — Kubernetes / k3s cluster housekeeping."""

from __future__ import annotations

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.models.k3s import K3sImage
from janitor.services.k3s import K3sQueryError, K3sService
from janitor.utils.console import console, err_console
from janitor.utils.format import format_bytes
from janitor.utils.prompt import confirm

app = typer.Typer(no_args_is_help=True, help="Kubernetes / k3s cluster housekeeping.")

STALE_HELP = "Also superseded tagged images: unused while another image of the repo is in use."


def _service(state: AppState) -> K3sService:
    return K3sService(runner=state.runner, config=state.config.k3s)


def _load_images(service: K3sService) -> list[K3sImage]:
    try:
        with console.status("[info]Reading runtime images and workloads...", spinner="dots"):
            return service.images()
    except K3sQueryError as exc:
        err_console.print(f"[err]{exc}[/]")
        err_console.print("[muted]Nothing removed: in-use images can't be told apart.[/muted]")
        raise typer.Exit(code=1) from exc


def _status(image: K3sImage) -> str:
    if image.in_use:
        return "[ok]in use[/]"
    if image.dangling:
        return "[warn]dangling[/]"
    if image.stale:
        return "[warn]stale[/]"
    return "[muted]unused (kept)[/muted]"


@app.command()
def images(
    ctx: typer.Context,
    stale: bool = typer.Option(False, "--stale", help="Only show what `prune --stale` removes."),
) -> None:
    """List cluster runtime images: in use, dangling, stale or kept.

    "In use" means named by any workload spec (CronJobs included), used by a
    container in any state, or pinned by the runtime.
    """
    state: AppState = ctx.obj
    all_images = _load_images(_service(state))
    shown = [i for i in all_images if i.prunable(stale=True)] if stale else all_images
    table = Table(title="k3s images", title_style="heading")
    table.add_column("Image")
    table.add_column("ID", style="muted")
    table.add_column("Size*", justify="right")
    table.add_column("Status")
    for image in sorted(shown, key=lambda i: (i.in_use, i.reference)):
        table.add_row(image.reference, image.short_id, format_bytes(image.size), _status(image))
    console.print(table)
    safe = [i for i in all_images if i.prunable(stale=False)]
    more = [i for i in all_images if i.prunable(stale=True) and not i.prunable(stale=False)]
    console.print(
        f"{len(all_images)} images. `prune`: {len(safe)} dangling "
        f"({format_bytes(sum(i.size for i in safe))}); `prune --stale` adds {len(more)} "
        f"({format_bytes(sum(i.size for i in more))})."
    )
    console.print("[muted]* compressed size; unpacked layers are freed too.[/muted]")


@app.command()
def prune(
    ctx: typer.Context,
    stale: bool = typer.Option(False, "--stale", help=STALE_HELP),
) -> None:
    """Remove unused dangling images (and, with --stale, superseded ones)."""
    state: AppState = ctx.obj
    service = _service(state)
    targets = [i for i in _load_images(service) if i.prunable(stale=stale)]
    if not targets:
        console.print("[ok]Nothing to prune.[/]")
        return
    total = format_bytes(sum(i.size for i in targets))
    console.print(f"About to remove {len(targets)} image(s) (at least {total}):")
    for image in targets:
        console.print(f"  [muted]{image.reference} ({format_bytes(image.size)})[/muted]")
    if state.dry_run:
        console.print("[warn]Dry-run: no changes will be made.[/]")
    elif not confirm(f"Remove {len(targets)} image(s)?", assume_yes=state.assume_yes):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)
    removed = service.remove_images(targets)
    verb = "Would remove" if state.dry_run else "Removed"
    console.print(f"[ok]{verb} {len(removed)} of {len(targets)} image(s).[/]")
    if service.is_rancher and removed and not state.dry_run:
        console.print("[info]Run `jt k3s trim` to give the space back to the host.[/]")


@app.command()
def trim(ctx: typer.Context) -> None:
    """fstrim the k3s VM so space freed inside it returns to the host disk."""
    state: AppState = ctx.obj
    try:
        with console.status("[info]Trimming VM filesystems...", spinner="dots"):
            output = _service(state).trim()
    except K3sQueryError as exc:
        err_console.print(f"[err]{exc}[/]")
        raise typer.Exit(code=1) from exc
    if state.dry_run:
        console.print("[warn]Dry-run: fstrim not run.[/]")
        return
    console.print(output or "[ok]Trimmed.[/]")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show node and pod health for the active cluster."""
    state: AppState = ctx.obj
    service = _service(state)
    with console.status("[info]Querying cluster...", spinner="dots"):
        cluster = service.status()
    if not cluster.available:
        err_console.print(
            "[err]No reachable Kubernetes cluster (kubectl missing or no context).[/]"
        )
        raise typer.Exit(code=1)

    console.print(f"[info]Context:[/] {cluster.context or 'unknown'}")

    node_table = Table(title="Nodes", title_style="heading")
    node_table.add_column("Name")
    node_table.add_column("Ready", justify="center")
    node_table.add_column("Roles")
    node_table.add_column("Version", style="muted")
    for node in cluster.nodes:
        ready = "[ok]Ready[/]" if node.ready else "[err]NotReady[/]"
        node_table.add_row(node.name, ready, node.roles, node.version)
    console.print(node_table)

    failed = cluster.failed_pods
    summary_style = "err" if failed else "ok"
    console.print(f"[{summary_style}]Pods: {len(cluster.pods)} total, {len(failed)} unhealthy.[/]")
    if failed:
        pod_table = Table(title="Unhealthy Pods", title_style="heading")
        pod_table.add_column("Namespace")
        pod_table.add_column("Pod")
        pod_table.add_column("Phase")
        pod_table.add_column("Restarts", justify="right")
        for pod in failed:
            pod_table.add_row(
                pod.namespace,
                pod.name,
                f"[warn]{pod.phase}[/]",
                str(pod.restarts),
            )
        console.print(pod_table)


@app.command()
def cleanup(ctx: typer.Context) -> None:
    """Delete completed (succeeded) jobs across all namespaces."""
    state: AppState = ctx.obj
    service = _service(state)
    if not service.is_available():
        err_console.print("[err]No reachable Kubernetes cluster.[/]")
        raise typer.Exit(code=1)
    if not state.dry_run and not confirm("Delete all completed jobs?", assume_yes=state.assume_yes):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)
    with console.status("[info]Cleaning up completed jobs...", spinner="dots"):
        deleted = service.cleanup_completed_jobs()
    if not deleted:
        console.print("[ok]No completed jobs to clean up.[/]")
        return
    verb = "Would delete" if state.dry_run else "Deleted"
    console.print(f"[ok]{verb} {len(deleted)} job(s):[/]")
    for name in deleted:
        console.print(f"  [muted]{name}[/muted]")
