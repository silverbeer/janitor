"""``jt secrets`` — shared Varlock + 1Password secret resolution."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

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


@app.command()
def init(
    ctx: typer.Context,
    app_name: str = typer.Argument(None, help="App name (defaults to the directory name)."),
    path: Path = typer.Option(
        Path("."), "--path", help="Repo root to scaffold the .env.schema in."
    ),
) -> None:
    """Scaffold a repo .env.schema that imports the shared base, then make it committable."""
    state: AppState = ctx.obj
    service = SecretsService(runner=state.runner)
    target = path.expanduser().resolve()
    name = app_name or target.name

    base = service.write_base_schema()
    console.print(f"[muted]Base schema:[/] {base}")

    schema_path, status = service.scaffold_schema(target, name)
    if status == "exists":
        console.print(f"[warn]Left existing[/] {schema_path} [muted](not overwritten)[/]")
    elif status == "would-create":
        console.print(f"[muted]Would create[/] {schema_path}")
    else:
        console.print(f"[ok]Created[/] {schema_path}")

    gi_status = service.ensure_gitignore_allows(target)
    if gi_status == "fixed":
        console.print("[ok]Added[/] !.env.schema to .gitignore [muted](was ignored)[/]")
    elif gi_status == "would-fix":
        console.print("[muted]Would add[/] !.env.schema to .gitignore")

    console.print("[muted]Next: add your vars to the schema, then[/] jt secrets run -- <cmd>")


@app.command()
def doctor(
    ctx: typer.Context,
    path: Path = typer.Option(Path("."), "--path", help="Repo root (holds .env.schema)."),
    helm: Path = typer.Option(None, "--helm", help="Helm dir to scan (defaults to <root>/helm)."),
) -> None:
    """Lint name parity between .env.schema and the repo's k8s secret wiring."""
    state: AppState = ctx.obj
    service = SecretsService(runner=state.runner)
    root = path.expanduser().resolve()
    schema_path = root / ".env.schema"
    if not schema_path.is_file():
        err_console.print(f"[err]No .env.schema in {root}[/] — run jt secrets init first.")
        raise typer.Exit(code=1)
    helm_dir = helm.expanduser().resolve() if helm else root / "helm"

    schema_vars = service.parse_schema_vars(schema_path)
    cloud_vars = service.helm_secret_env_vars(helm_dir)
    report = service.parity(schema_vars, cloud_vars)

    table = Table(title=f"Secret parity — {root.name}", title_style="heading", expand=True)
    table.add_column("Variable")
    table.add_column("Schema", justify="center")
    table.add_column("Cloud (k8s)", justify="center")
    yes, no = "[ok]✓[/]", "[muted]—[/]"
    for name in sorted(schema_vars | cloud_vars):
        table.add_row(name, yes if name in schema_vars else no, yes if name in cloud_vars else no)
    console.print(table)

    if not cloud_vars:
        console.print(f"[muted]No secretKeyRef env vars found under {helm_dir}.[/muted]")
    if report.only_cloud:
        console.print(
            f"[err]⚠ in cloud but not in schema:[/] {', '.join(report.only_cloud)} "
            "— add them to .env.schema so local + cloud agree."
        )
    if report.healthy and cloud_vars:
        console.print("[ok]Every cloud secret var is declared in the schema.[/]")
