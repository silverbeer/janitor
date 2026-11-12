"""``jt k3s`` — Kubernetes / k3s cluster housekeeping."""

from __future__ import annotations

import sys

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.models.k3s import K3sImage, K3sMaintainResult
from janitor.services.k3s import (
    K3sQueryError,
    K3sService,
    load_maintain_result,
    save_maintain_result,
)
from janitor.services.schedule import LaunchAgent, build_plist
from janitor.services.shell import which
from janitor.utils.console import console, err_console
from janitor.utils.format import format_bytes
from janitor.utils.prompt import confirm

app = typer.Typer(no_args_is_help=True, help="Kubernetes / k3s cluster housekeeping.")

schedule_app = typer.Typer(no_args_is_help=True, help="Run `jt k3s maintain` weekly via launchd.")
app.add_typer(schedule_app, name="schedule")

LAUNCHD_LABEL = "com.silverbeer.janitor.k3s-maintain"
WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

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
    cache = _service(state).build_cache()
    if cache is not None:
        console.print(
            f"BuildKit build cache: {format_bytes(cache.total)} in {cache.records} record(s), "
            f"{format_bytes(cache.reclaimable)} reclaimable by `prune`."
        )


@app.command()
def prune(
    ctx: typer.Context,
    stale: bool = typer.Option(False, "--stale", help=STALE_HELP),
    build_cache: bool = typer.Option(
        True,
        "--build-cache/--no-build-cache",
        help="Also drop the node's unused BuildKit build cache (local `nerdctl build` layers).",
    ),
) -> None:
    """Remove unused dangling images (and, with --stale, superseded ones) and build cache."""
    state: AppState = ctx.obj
    service = _service(state)
    targets = [i for i in _load_images(service) if i.prunable(stale=stale)]
    cache = service.build_cache() if build_cache else None
    cache_bytes = cache.reclaimable if cache else 0
    if not targets and not cache_bytes:
        console.print("[ok]Nothing to prune.[/]")
        return
    if targets:
        total = format_bytes(sum(i.size for i in targets))
        console.print(f"About to remove {len(targets)} image(s) (at least {total}):")
        for image in targets:
            console.print(f"  [muted]{image.reference} ({format_bytes(image.size)})[/muted]")
    if cache and cache_bytes:
        console.print(
            f"About to drop BuildKit build cache: {format_bytes(cache_bytes)} "
            f"({cache.records} record(s)); next local build is slower."
        )
    what = " and ".join(
        part
        for part in (
            f"{len(targets)} image(s)" if targets else "",
            "the build cache" if cache_bytes else "",
        )
        if part
    )
    if state.dry_run:
        console.print("[warn]Dry-run: no changes will be made.[/]")
    elif not confirm(f"Remove {what}?", assume_yes=state.assume_yes):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)
    verb = "Would remove" if state.dry_run else "Removed"
    removed: list[K3sImage] = []
    if targets:
        removed = service.remove_images(targets)
        console.print(f"[ok]{verb} {len(removed)} of {len(targets)} image(s).[/]")
    freed = 0
    if cache_bytes:
        try:
            freed = service.prune_build_cache()
        except K3sQueryError as exc:
            err_console.print(f"[err]{exc}[/]")
            raise typer.Exit(code=1) from exc
        shown = cache_bytes if state.dry_run else freed
        console.print(f"[ok]{verb} {format_bytes(shown)} of build cache.[/]")
    if service.is_rancher and (removed or freed) and not state.dry_run:
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


# ---------------------------------------------------------------- maintain


def _summary(result: K3sMaintainResult) -> str:
    return (
        f"{result.images_removed} image(s) ({format_bytes(result.image_bytes)}), "
        f"{format_bytes(result.build_cache_freed)} build cache; "
        f"{len(result.failures)} failure(s)"
    )


@app.command()
def maintain(ctx: typer.Context) -> None:
    """Unattended upkeep: prune dangling images + build cache, then trim the VM.

    What the weekly schedule runs. Never removes `--stale` images. Each step
    runs even if an earlier one failed; the result is recorded for `jt doctor`.
    """
    state: AppState = ctx.obj
    if not state.dry_run and not confirm(
        "Prune dangling images and build cache, then trim the VM?", assume_yes=state.assume_yes
    ):
        console.print("[muted]Aborted.[/muted]")
        raise typer.Exit(code=0)
    with console.status("[info]Maintaining k3s node...", spinner="dots"):
        result = _service(state).maintain()
    save_maintain_result(result, state.config.k3s.state_file)
    verb = "Would free" if result.dry_run else "Freed"
    console.print(f"[ok]{verb}:[/] {_summary(result)}")
    if result.trim:
        console.print(f"[muted]{result.trim}[/muted]")
    for line in result.failures:
        err_console.print(f"[err]✗ {line}[/]")
    if result.failures:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------- schedule


def _when(state: AppState) -> str:
    cfg = state.config.k3s
    return (
        f"weekly on {WEEKDAYS[cfg.schedule_weekday]} at "
        f"{cfg.schedule_hour:02d}:{cfg.schedule_minute:02d}"
    )


@schedule_app.command("install")
def schedule_install(ctx: typer.Context) -> None:
    """Install (or refresh) the weekly `jt --yes k3s maintain` LaunchAgent."""
    state: AppState = ctx.obj
    cfg = state.config.k3s
    if sys.platform != "darwin":
        console.print("[err]launchd scheduling is macOS-only.[/]")
        raise typer.Exit(code=1)
    jt = which("jt")
    if jt is None:
        console.print("[err]`jt` is not on PATH — install it with `uv tool install` first.[/]")
        raise typer.Exit(code=1)
    agent = LaunchAgent(state.runner, LAUNCHD_LABEL)
    plist = build_plist(
        label=LAUNCHD_LABEL,
        program=[jt, "--yes", "k3s", "maintain"],
        weekday=cfg.schedule_weekday,
        hour=cfg.schedule_hour,
        minute=cfg.schedule_minute,
        log_file=cfg.log_file,
    )
    if not agent.install(plist, log_file=cfg.log_file):
        console.print("[err]launchctl refused the job — see `launchctl print`.[/]")
        raise typer.Exit(code=1)
    console.print(
        f"[ok]Scheduled {_when(state)}.[/] [muted]{agent.plist_path} → log {cfg.log_file}[/muted]"
    )


@schedule_app.command("status")
def schedule_status(ctx: typer.Context) -> None:
    """Show whether the job is loaded and what the last real run did."""
    state: AppState = ctx.obj
    cfg = state.config.k3s
    agent = LaunchAgent(state.runner, LAUNCHD_LABEL)
    loaded = agent.loaded()
    console.print(
        f"LaunchAgent: {'[ok]loaded[/]' if loaded else '[warn]not loaded[/]'} "
        f"[muted]({agent.plist_path})[/muted]"
    )
    console.print(f"Runs {_when(state)}; log {cfg.log_file}")
    last = load_maintain_result(cfg.state_file)
    if last is None:
        console.print("[warn]No recorded run yet.[/]")
        return
    console.print(f"Last run {last.ran_at}: {_summary(last)}.")


@schedule_app.command("uninstall")
def schedule_uninstall(ctx: typer.Context) -> None:
    """Remove the LaunchAgent."""
    state: AppState = ctx.obj
    removed = LaunchAgent(state.runner, LAUNCHD_LABEL).uninstall()
    console.print("[ok]Removed.[/]" if removed else "[muted]Nothing was installed.[/muted]")
