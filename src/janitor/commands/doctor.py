"""``jt doctor`` — system health summary."""

from __future__ import annotations

import typer
from rich.panel import Panel
from rich.table import Table

from janitor.context import AppState
from janitor.models.common import HealthStatus
from janitor.services.disk import DiskService
from janitor.services.git import last_run_age_days, load_result
from janitor.services.k3s import K3sService
from janitor.services.system import SystemService
from janitor.utils.console import console
from janitor.utils.format import format_bytes

__all__ = ["doctor"]


def doctor(ctx: typer.Context) -> None:
    """Run a full system health check and print a Rich summary."""
    state: AppState = ctx.obj
    system = SystemService(runner=state.runner)
    disk = DiskService()
    disk_config = state.config.disk

    with console.status("[info]Running health checks...", spinner="dots"):
        checks = system.all_checks()
        usage = disk.usage()

    table = Table(title="Janitor Doctor", title_style="heading", expand=True)
    table.add_column("Component", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Version / Detail", style="muted")

    worst = HealthStatus.OK
    for check in checks:
        table.add_row(
            check.name,
            f"[{check.status.style}]{check.status.icon} {check.status.value}[/]",
            check.version or check.detail or "",
        )
        if check.status is HealthStatus.ERROR:
            worst = HealthStatus.ERROR
        elif check.status is HealthStatus.WARN and worst is not HealthStatus.ERROR:
            worst = HealthStatus.WARN

    # The row and the verdict read the same classification. They used not to:
    # the row went red at >=90% while the summary still said "All systems
    # healthy", so a machine at 97.9% reported as fine (SB-861).
    disk_health = usage.health(
        warn_percent=disk_config.warn_percent,
        error_percent=disk_config.error_percent,
    )
    if disk_health is HealthStatus.ERROR:
        worst = HealthStatus.ERROR
    elif disk_health is HealthStatus.WARN and worst is not HealthStatus.ERROR:
        worst = HealthStatus.WARN

    table.add_row(
        "Disk (/)",
        f"[{disk_health.style}]{usage.percent_used}% used[/]",
        f"{format_bytes(usage.free)} free of {format_bytes(usage.total)}",
    )

    # Git hygiene (SB-1580): read the last scheduled run, never scan here —
    # doctor stays fast and offline.
    git_cfg = state.config.git
    age = last_run_age_days(git_cfg.state_file)
    last = load_result(git_cfg.state_file)
    if age is None or last is None:
        # Unknown, not a warning: a machine that never scheduled it (a laptop)
        # is not unhealthy, and the verdict should not say so.
        git_health = HealthStatus.UNKNOWN
        git_detail = "no recorded prune — `jt git schedule install`"
    else:
        stale = age > git_cfg.stale_after_days
        git_health = HealthStatus.WARN if stale or last.failures else HealthStatus.OK
        git_detail = (
            f"last prune {age:.1f}d ago: {len(last.branches_deleted)} branch(es), "
            f"{len(last.worktrees_removed)} worktree(s) removed; {len(last.review)} to review"
            + (f"; {len(last.failures)} failure(s)" if last.failures else "")
        )
    if git_health is HealthStatus.WARN and worst is HealthStatus.OK:
        worst = HealthStatus.WARN
    table.add_row(
        "Git hygiene",
        f"[{git_health.style}]{git_health.icon} {git_health.value}[/]",
        git_detail,
    )

    # k3s build cache (SB-1631): BuildKit layers from local builds live outside
    # the CRI, so image pruning never sees them; 54 GB filled the disk unseen.
    k3s = K3sService(runner=state.runner, config=state.config.k3s)
    if k3s.buildkit_addr is not None:
        cache = k3s.build_cache()
        limit = int(state.config.k3s.build_cache_warn_gb * 1024**3)
        if cache is None:
            cache_health = HealthStatus.UNKNOWN
            cache_detail = "buildkitd unreachable (VM stopped?)"
        else:
            cache_health = HealthStatus.WARN if cache.reclaimable > limit else HealthStatus.OK
            cache_detail = f"{format_bytes(cache.reclaimable)} reclaimable" + (
                " — `jt --yes k3s prune && jt --yes k3s trim`"
                if cache_health is HealthStatus.WARN
                else ""
            )
        if cache_health is HealthStatus.WARN and worst is HealthStatus.OK:
            worst = HealthStatus.WARN
        table.add_row(
            "k3s build cache",
            f"[{cache_health.style}]{cache_health.icon} {cache_health.value}[/]",
            cache_detail,
        )

    console.print(table)
    summary = {
        HealthStatus.OK: "[ok]All systems healthy.[/]",
        HealthStatus.WARN: "[warn]Some components need attention.[/]",
        HealthStatus.ERROR: "[err]Critical issues detected.[/]",
    }[worst]
    console.print(Panel(summary, border_style=worst.style, title="Summary"))
