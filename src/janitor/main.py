"""Janitor CLI entry point — wires global options and command groups."""

from __future__ import annotations

import typer
from rich.panel import Panel

from janitor.commands import brew, disk, docker, k3s, logs, supabase
from janitor.commands.doctor import doctor as doctor_command
from janitor.config import load_config
from janitor.context import AppState
from janitor.logging import configure_logging, get_logger
from janitor.services.shell import ShellRunner
from janitor.utils.console import console
from janitor.version import __version__

app = typer.Typer(
    name="jt",
    help="Janitor — a Swiss Army knife for workstation and platform housekeeping.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

logger = get_logger("janitor")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[accent]Janitor[/] [bold]{__version__}[/]")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview mutating actions without executing them."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm prompts (for automation)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging."),
    log_json: bool = typer.Option(False, "--log-json", help="Emit logs as JSON."),
    version: bool = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the Janitor version and exit.",
    ),
) -> None:
    """Initialize shared state and logging for every subcommand."""
    config = load_config()
    if dry_run:
        config.dry_run = True
    if yes:
        config.assume_yes = True
    if verbose:
        config.log_level = "DEBUG"
    if log_json:
        config.log_json = True

    configure_logging(config.log_level, json=config.log_json)
    ctx.obj = AppState(config=config, runner=ShellRunner(dry_run=config.dry_run))


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Run a full system health check."""
    doctor_command(ctx)


@app.command()
def version() -> None:
    """Show the Janitor version."""
    console.print(
        Panel(
            f"[accent]Janitor[/] [bold]{__version__}[/]\n"
            "[muted]Workstation & platform housekeeping[/muted]",
            border_style="accent",
        )
    )


app.add_typer(docker.app, name="docker")
app.add_typer(disk.app, name="disk")
app.add_typer(brew.app, name="brew")
app.add_typer(logs.app, name="logs")
app.add_typer(supabase.app, name="supabase")
app.add_typer(k3s.app, name="k3s")


if __name__ == "__main__":  # pragma: no cover
    app()
