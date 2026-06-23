"""``jt k3s`` — Kubernetes / k3s cluster housekeeping."""

from __future__ import annotations

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.services.k3s import K3sService
from janitor.utils.console import console, err_console
from janitor.utils.prompt import confirm

app = typer.Typer(no_args_is_help=True, help="Kubernetes / k3s cluster housekeeping.")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show node and pod health for the active cluster."""
    state: AppState = ctx.obj
    service = K3sService(runner=state.runner)
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
    service = K3sService(runner=state.runner)
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
