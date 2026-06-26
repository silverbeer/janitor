"""``jt secrets`` — shared Varlock + 1Password secret resolution."""

from __future__ import annotations

import typer

from janitor.context import AppState
from janitor.services.secrets import SecretsService
from janitor.utils.console import console, err_console

app = typer.Typer(no_args_is_help=True, help="Shared secret resolution (Varlock + 1Password).")


@app.command()
def base(ctx: typer.Context) -> None:
    """Write/refresh the shared base schema that each repo's .env.schema imports."""
    state: AppState = ctx.obj
    service = SecretsService(runner=state.runner)
    path = service.write_base_schema()
    verb = "Would write" if state.dry_run else "Wrote"
    console.print(f"[ok]{verb} base schema:[/] {path}")
    console.print(f"[muted]Import it from a repo's .env.schema:[/] # @import({path})")


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def run(ctx: typer.Context) -> None:
    """Run a command under ``varlock run`` so its env resolves from 1Password.

    Everything after the command name is passed through, e.g.:
    ``jt secrets run -- jt supabase sync-users stk``.
    """
    state: AppState = ctx.obj
    service = SecretsService(runner=state.runner)
    command = list(ctx.args)
    if not command:
        err_console.print("[err]Nothing to run.[/] Usage: jt secrets run -- <command>")
        raise typer.Exit(code=2)
    if not service.varlock_available():
        err_console.print(
            "[err]varlock not found.[/] Install it:\n  [bold]npm install -g varlock[/]"
        )
        raise typer.Exit(code=1)
    code = service.run(command)
    raise typer.Exit(code=code)
