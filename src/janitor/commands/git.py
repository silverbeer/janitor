"""``jt git`` — merged branch and worktree hygiene (SB-1580).

``audit`` is read-only. ``prune`` deletes only what ``audit`` marks PRUNE and
never touches REVIEW items. ``schedule`` runs ``jt --yes git prune`` daily.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.table import Table

from janitor.context import AppState
from janitor.models.git import GitPruneResult, RepoReport, Verdict
from janitor.services.git import (
    GitService,
    load_result,
    new_result,
    save_result,
)
from janitor.services.schedule import LaunchAgent, build_plist
from janitor.services.shell import which
from janitor.utils.console import console
from janitor.utils.format import format_bytes
from janitor.utils.prompt import confirm

app = typer.Typer(no_args_is_help=True, help="Merged branch and worktree cleanup.")
schedule_app = typer.Typer(no_args_is_help=True, help="Run `jt git prune` daily via launchd.")
app.add_typer(schedule_app, name="schedule")

LAUNCHD_LABEL = "com.silverbeer.janitor.git-prune"

_REPO_OPTION = typer.Option(
    None,
    "--repo",
    "-r",
    help="Limit to the repo containing this path (e.g. `.`). Default: every repo under git.roots.",
)
_FETCH_OPTION = typer.Option(True, "--fetch/--no-fetch", help="`git fetch --prune` first.")


def _service(state: AppState) -> GitService:
    cfg = state.config.git
    return GitService(
        state.runner,
        artifacts=cfg.artifacts,
        protected_branches=cfg.protected_branches,
        protected_paths=cfg.protected_paths,
    )


def _repos(state: AppState, service: GitService, repo: Path | None) -> list[Path]:
    if repo is not None:
        main = service.main_checkout(repo)
        if main is None:
            console.print(f"[err]{repo} is not inside a git repository.[/]")
            raise typer.Exit(code=1)
        return [main]
    cfg = state.config.git
    return service.discover(cfg.roots, cfg.exclude_repos)


def _scan(
    state: AppState, repo: Path | None, *, fetch: bool, sizes: bool = True
) -> list[RepoReport]:
    service = _service(state)
    repos = _repos(state, service, repo)
    reports: list[RepoReport] = []
    with console.status("[info]Scanning repositories...", spinner="dots") as status:
        for path in repos:
            status.update(f"[info]Scanning {path.name}...")
            reports.append(service.scan(path, fetch=fetch, sizes=sizes))
    return reports


def _summary_table(reports: list[RepoReport]) -> Table:
    table = Table(title="Git hygiene", title_style="heading", expand=True)
    table.add_column("Repo", style="bold")
    table.add_column("Branches", justify="right")
    table.add_column("Prune", justify="right", style="ok")
    table.add_column("Review", justify="right", style="warn")
    table.add_column("Worktrees", justify="right")
    table.add_column("Prune", justify="right", style="ok")
    table.add_column("Review", justify="right", style="warn")
    table.add_column("Reclaim", justify="right", style="accent")
    for r in reports:
        table.add_row(
            r.name + (" [err](error)[/]" if r.errors else ""),
            str(len(r.branches)),
            str(len(r.branches_with(Verdict.PRUNE)) or ""),
            str(len(r.branches_with(Verdict.REVIEW)) or ""),
            str(len(r.worktrees)),
            str(len(r.worktrees_with(Verdict.PRUNE)) or ""),
            str(len(r.worktrees_with(Verdict.REVIEW)) or ""),
            format_bytes(r.reclaimable_bytes) if r.reclaimable_bytes else "",
        )
    return table


def _details(reports: list[RepoReport], verdicts: set[Verdict]) -> None:
    for r in reports:
        rows = [
            (w.verdict, f"worktree {w.path}", w.reason, w.size_bytes)
            for w in r.worktrees
            if w.verdict in verdicts
        ] + [(b.verdict, b.name, b.reason, None) for b in r.branches if b.verdict in verdicts]
        if not rows and not r.errors:
            continue
        table = Table(title=r.name, title_style="heading", expand=True, show_header=False)
        table.add_column("Verdict", width=8)
        table.add_column("What")
        table.add_column("Why", style="muted")
        table.add_column("Size", justify="right")
        for error in r.errors:
            table.add_row("[err]error[/]", error, "repo will not be pruned", "")
        for verdict, what, why, size in rows:
            table.add_row(
                f"[{verdict.style}]{verdict.value}[/]",
                what,
                why,
                format_bytes(size) if size else "",
            )
        console.print(table)


@app.command()
def audit(
    ctx: typer.Context,
    repo: Path | None = _REPO_OPTION,
    fetch: bool = _FETCH_OPTION,
    show_all: bool = typer.Option(False, "--all", help="Also list KEEP items."),
) -> None:
    """Report merged branches/worktrees (PRUNE) and stale ones needing a human (REVIEW).

    Read-only — fetches, but never deletes.
    """
    state: AppState = ctx.obj
    reports = _scan(state, repo, fetch=fetch)
    if not reports:
        console.print("[warn]No git repositories found under git.roots.[/]")
        return
    console.print(_summary_table(reports))
    verdicts = {Verdict.PRUNE, Verdict.REVIEW} | ({Verdict.KEEP} if show_all else set())
    _details(reports, verdicts)
    prune_n = sum(
        len(r.branches_with(Verdict.PRUNE)) + len(r.worktrees_with(Verdict.PRUNE)) for r in reports
    )
    if prune_n:
        console.print(f"[accent]{prune_n} item(s) can be pruned:[/] jt git prune")


@app.command()
def prune(
    ctx: typer.Context,
    repo: Path | None = _REPO_OPTION,
    fetch: bool = _FETCH_OPTION,
) -> None:
    """Delete merged branches and remove merged, clean worktrees.

    Only PRUNE items are touched. REVIEW items (closed-unmerged PRs, commits after
    merge, uncommitted changes) are listed and left alone. A repo whose scan had
    an error is skipped whole.
    """
    state: AppState = ctx.obj
    reports = _scan(state, repo, fetch=fetch)
    service = _service(state)
    result = new_result(dry_run=state.dry_run)
    result.repos = len(reports)
    for r in reports:
        service.collect_review(r, result)

    branch_n = sum(len(r.branches_with(Verdict.PRUNE)) for r in reports if not r.errors)
    tree_n = sum(len(r.worktrees_with(Verdict.PRUNE)) for r in reports if not r.errors)
    reclaim = sum(r.reclaimable_bytes for r in reports if not r.errors)

    _details(reports, {Verdict.PRUNE})
    if branch_n or tree_n:
        console.print(
            f"[warn]{branch_n} branch(es) and {tree_n} worktree(s) "
            f"({format_bytes(reclaim)}) will be removed.[/]"
        )
        if state.dry_run:
            console.print("[muted]Dry-run: nothing will be deleted.[/muted]")
        elif not confirm("Prune them?", assume_yes=state.assume_yes):
            console.print("[muted]Aborted.[/muted]")
            raise typer.Exit(code=0)
        for r in reports:
            service.prune(r, result)
    else:
        console.print("[ok]Nothing to prune.[/]")
        for r in reports:
            if r.errors:
                result.failures.append(f"{r.name}: skipped — {'; '.join(r.errors)}")

    _print_result(result)
    save_result(result, state.config.git.state_file)
    if result.failures:
        raise typer.Exit(code=1)


def _print_result(result: GitPruneResult) -> None:
    verb = "Would remove" if result.dry_run else "Removed"
    if not result.dry_run and (result.branches_deleted or result.worktrees_removed):
        console.print(
            f"[ok]{verb} {len(result.branches_deleted)} branch(es), "
            f"{len(result.worktrees_removed)} worktree(s), "
            f"{format_bytes(result.bytes_reclaimed)} reclaimed.[/]"
        )
    if result.review:
        console.print(f"[warn]{len(result.review)} item(s) need a human (REVIEW):[/]")
        for line in result.review:
            console.print(f"  [warn]•[/] {line}")
    for line in result.failures:
        console.print(f"[err]✗ {line}[/]")


# ---------------------------------------------------------------- schedule


@schedule_app.command("install")
def schedule_install(ctx: typer.Context) -> None:
    """Install (or refresh) the daily `jt --yes git prune` LaunchAgent."""
    state: AppState = ctx.obj
    cfg = state.config.git
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
        program=[jt, "--yes", "git", "prune"],
        hour=cfg.schedule_hour,
        minute=cfg.schedule_minute,
        log_file=cfg.log_file,
    )
    if not agent.install(plist, log_file=cfg.log_file):
        console.print("[err]launchctl refused the job — see `launchctl print`.[/]")
        raise typer.Exit(code=1)
    console.print(
        f"[ok]Scheduled daily at {cfg.schedule_hour:02d}:{cfg.schedule_minute:02d}.[/] "
        f"[muted]{agent.plist_path} → log {cfg.log_file}[/muted]"
    )


@schedule_app.command("status")
def schedule_status(ctx: typer.Context) -> None:
    """Show whether the job is loaded and what the last real run did."""
    state: AppState = ctx.obj
    cfg = state.config.git
    agent = LaunchAgent(state.runner, LAUNCHD_LABEL)
    loaded = agent.loaded()
    console.print(
        f"LaunchAgent: {'[ok]loaded[/]' if loaded else '[warn]not loaded[/]'} "
        f"[muted]({agent.plist_path})[/muted]"
    )
    console.print(
        f"Runs daily at {cfg.schedule_hour:02d}:{cfg.schedule_minute:02d}; log {cfg.log_file}"
    )
    last = load_result(cfg.state_file)
    if last is None:
        console.print("[warn]No recorded run yet.[/]")
        return
    console.print(
        f"Last run {last.ran_at}: {len(last.branches_deleted)} branch(es), "
        f"{len(last.worktrees_removed)} worktree(s), {format_bytes(last.bytes_reclaimed)}; "
        f"{len(last.review)} to review, {len(last.failures)} failure(s)."
    )


@schedule_app.command("uninstall")
def schedule_uninstall(ctx: typer.Context) -> None:
    """Remove the LaunchAgent."""
    state: AppState = ctx.obj
    removed = LaunchAgent(state.runner, LAUNCHD_LABEL).uninstall()
    console.print("[ok]Removed.[/]" if removed else "[muted]Nothing was installed.[/muted]")
