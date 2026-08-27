"""``jt doctor`` — system health summary."""

from __future__ import annotations

import typer
from rich.panel import Panel
from rich.table import Table

from janitor.context import AppState
from janitor.models.common import HealthStatus
from janitor.services.disk import DiskService
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

    console.print(table)
    summary = {
        HealthStatus.OK: "[ok]All systems healthy.[/]",
        HealthStatus.WARN: "[warn]Some components need attention.[/]",
        HealthStatus.ERROR: "[err]Critical issues detected.[/]",
    }[worst]
    console.print(Panel(summary, border_style=worst.style, title="Summary"))
