"""Git housekeeping — find and prune merged branches and worktrees (SB-1580).

The one rule this module exists to keep: **never delete work that exists
nowhere else.** Everything else is bookkeeping around that.

So a branch is only pruned when one of these is provably true:

* its pull request is MERGED **and** the local tip is already contained in
  that PR's head or in ``origin/<default>`` (nothing committed after merge); or
* it has no PR, its remote branch was deleted, **and** its tip is an ancestor
  of ``origin/<default>`` (a true merge, the commits are on main).

A worktree is only removed when it is unlocked, attached to such a branch, and
has no changes other than known build artefacts (``.venv``, ``node_modules``…).

Anything that *might* be stale but could hold work — a PR closed without
merging, commits after merge, a dirty worktree — is ``REVIEW``: reported every
run, never deleted. A repo whose scan hit an error is not pruned at all.

The decision logic is pure (:func:`classify_branch`, :func:`classify_worktree`)
so it is tested without git; :class:`GitService` only gathers facts and acts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from janitor.logging import get_logger
from janitor.models.git import (
    BranchInfo,
    GitPruneResult,
    PrInfo,
    RepoReport,
    Verdict,
    WorktreeInfo,
)
from janitor.services.shell import ShellRunner

__all__ = [
    "DEFAULT_ARTIFACTS",
    "GitService",
    "classify_branch",
    "classify_worktree",
    "last_run_age_days",
    "load_result",
    "new_result",
    "parse_gh_repo",
    "save_result",
    "split_status_paths",
]

logger = get_logger(__name__)

#: Untracked paths that are rebuilt on demand and never count as "work".
DEFAULT_ARTIFACTS = (
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".coverage",
    "htmlcov",
    "html-report",
    "test-results.json",
    "dist",
    "build",
)

_GH_REMOTE = re.compile(r"github\.com[:/](?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?/?$")


# --------------------------------------------------------------------------- pure


def parse_gh_repo(remote_url: str) -> str | None:
    """``owner/name`` for a GitHub remote URL (https or ssh), else None."""
    match = _GH_REMOTE.search(remote_url.strip())
    return match.group("repo") if match else None


def split_status_paths(
    porcelain: str, artifacts: Iterable[str] = DEFAULT_ARTIFACTS
) -> tuple[list[str], list[str]]:
    """Split ``git status --porcelain`` lines into (real work, build artefacts).

    A path is an artefact when any of its components is in ``artifacts`` —
    ``backend/.venv/`` and ``frontend/node_modules`` both qualify.
    """
    names = set(artifacts)
    dirty: list[str] = []
    junk: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:  # rename: judge the destination
            path = path.split(" -> ", 1)[1]
        parts = PurePosixPath(path.rstrip("/")).parts
        is_artifact = line.startswith("??") and any(p in names for p in parts)
        (junk if is_artifact else dirty).append(path)
    return dirty, junk


def _tip_is_safe(tip_in_pr_head: bool, tip_in_default: bool) -> bool:
    return tip_in_pr_head or tip_in_default


def classify_branch(
    branch: BranchInfo,
    *,
    default_branch: str,
    protected: set[str],
    checked_out: set[str],
    tip_in_pr_head: bool,
    tip_in_default: bool,
) -> tuple[Verdict, str]:
    """Decide what to do with one local branch.

    Args:
        checked_out: branches checked out in the main checkout or in a worktree
            that is *not* itself being pruned.
        tip_in_pr_head: the local tip is an ancestor of (or equal to) the PR's
            head commit — i.e. nothing was committed locally after the PR.
        tip_in_default: the local tip is an ancestor of ``origin/<default>``.
    """
    name = branch.name
    if name == default_branch or name in protected:
        return Verdict.KEEP, "protected branch"
    if name in checked_out:
        return Verdict.KEEP, "checked out"
    pr = branch.pr
    if pr is not None:
        if pr.state == "OPEN":
            return Verdict.KEEP, f"PR #{pr.number} open"
        if pr.state == "MERGED":
            if _tip_is_safe(tip_in_pr_head, tip_in_default):
                return Verdict.PRUNE, f"PR #{pr.number} merged"
            return Verdict.REVIEW, f"PR #{pr.number} merged, but local has newer commits"
        # CLOSED without merging
        if tip_in_default:
            return Verdict.PRUNE, f"PR #{pr.number} closed; commits already on {default_branch}"
        return Verdict.REVIEW, f"PR #{pr.number} closed without merging"
    if branch.upstream_gone:
        if tip_in_default:
            return Verdict.PRUNE, f"remote deleted; commits already on {default_branch}"
        return Verdict.REVIEW, "remote deleted, no PR found, commits not on default"
    if branch.upstream:
        return Verdict.KEEP, "pushed, no PR yet"
    return Verdict.KEEP, "local only, no PR"


def classify_worktree(
    worktree: WorktreeInfo,
    *,
    tip_in_pr_head: bool,
    tip_in_default: bool,
    protected_path: bool,
    is_cwd: bool,
) -> tuple[Verdict, str]:
    """Decide what to do with one linked worktree."""
    if not worktree.exists:
        return Verdict.PRUNE, "directory already gone (stale record)"
    if is_cwd:
        return Verdict.KEEP, "current directory"
    if protected_path:
        return Verdict.KEEP, "protected path"
    if worktree.locked:
        return Verdict.KEEP, "locked"
    if not worktree.branch:
        return Verdict.KEEP, "detached HEAD (a tool's checkout, not a feature worktree)"
    if worktree.dirty:
        return Verdict.REVIEW, f"{len(worktree.dirty)} uncommitted change(s)"
    pr = worktree.pr
    if pr is None:
        return Verdict.KEEP, "no PR yet"
    if pr.state == "OPEN":
        return Verdict.KEEP, f"PR #{pr.number} open"
    if pr.state == "MERGED":
        if _tip_is_safe(tip_in_pr_head, tip_in_default):
            return Verdict.PRUNE, f"PR #{pr.number} merged"
        return Verdict.REVIEW, f"PR #{pr.number} merged, but worktree has newer commits"
    return Verdict.REVIEW, f"PR #{pr.number} closed without merging"


# ----------------------------------------------------------------------- service


class GitService:
    """Gather git/GitHub facts, classify them, and act on PRUNE verdicts."""

    def __init__(
        self,
        runner: ShellRunner,
        *,
        artifacts: Iterable[str] = DEFAULT_ARTIFACTS,
        protected_branches: Iterable[str] = ("main", "master", "develop"),
        protected_paths: Iterable[Path] = (),
        cwd: Path | None = None,
    ) -> None:
        self.runner = runner
        self.artifacts = tuple(artifacts)
        self.protected_branches = set(protected_branches)
        self.protected_paths = [p.expanduser().resolve() for p in protected_paths]
        self.cwd = (cwd or Path.cwd()).resolve()

    # ---- discovery

    @staticmethod
    def discover(roots: Iterable[Path], exclude: Iterable[str] = ()) -> list[Path]:
        """Main checkouts directly under each root (``.git`` is a directory).

        A linked worktree has a ``.git`` *file*; it is reached through its main
        checkout, never scanned as a repo of its own.
        """
        skip = set(exclude)
        found: list[Path] = []
        for root in roots:
            root = root.expanduser()
            if not root.is_dir():
                continue
            candidates = [root, *sorted(p for p in root.iterdir() if p.is_dir())]
            for path in candidates:
                if path.name in skip:
                    continue
                if (path / ".git").is_dir():
                    found.append(path)
        return found

    def main_checkout(self, path: Path) -> Path | None:
        """The main checkout owning ``path`` (works from inside a worktree)."""
        result = self.runner.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
        if not result.ok or not result.stdout.strip():
            return None
        return Path(result.stdout.strip()).parent

    # ---- git helpers

    def _git(self, repo: Path, *args: str, timeout: float = 60.0) -> tuple[bool, str]:
        result = self.runner.run(["git", "-C", str(repo), *args], timeout=timeout)
        return result.ok, result.stdout

    def _is_ancestor(self, repo: Path, commit: str, of: str) -> bool:
        if not commit or not of:
            return False
        result = self.runner.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, of]
        )
        return result.returncode == 0

    def _pr_index(self, gh_repo: str) -> dict[str, PrInfo] | None:
        """head branch -> its most recent PR. None when GitHub can't be asked."""
        result = self.runner.run(
            [
                "gh",
                "pr",
                "list",
                "-R",
                gh_repo,
                "--state",
                "all",
                "--limit",
                "3000",
                "--json",
                "number,state,headRefName,headRefOid",
            ],
            timeout=120,
        )
        if not result.ok:
            return None
        try:
            rows = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return None
        index: dict[str, PrInfo] = {}
        for row in rows:
            head = row.get("headRefName") or ""
            pr = PrInfo(
                number=int(row["number"]),
                state=str(row.get("state", "")).upper(),
                head_oid=row.get("headRefOid") or "",
            )
            current = index.get(head)
            # An open PR always wins; otherwise the newest PR for the name.
            if (
                current is None
                or pr.state == "OPEN"
                or (current.state != "OPEN" and pr.number > current.number)
            ):
                index[head] = pr
        return index

    def _worktrees(self, repo: Path) -> list[WorktreeInfo]:
        ok, out = self._git(repo, "worktree", "list", "--porcelain")
        if not ok:
            return []
        trees: list[WorktreeInfo] = []
        current: dict[str, str | bool] = {}
        blocks = out.strip().split("\n\n")
        for index, block in enumerate(blocks):
            current = {}
            for line in block.splitlines():
                key, _, value = line.partition(" ")
                current[key] = value if value else True
            if index == 0:
                continue  # the main checkout itself
            path = Path(str(current.get("worktree", "")))
            branch = str(current.get("branch", "")).removeprefix("refs/heads/")
            trees.append(
                WorktreeInfo(
                    path=path,
                    branch="" if current.get("detached") else branch,
                    head=str(current.get("HEAD", "")),
                    locked="locked" in current,
                    exists=path.is_dir(),
                )
            )
        return trees

    def _main_branch(self, repo: Path) -> str:
        ok, out = self._git(repo, "branch", "--show-current")
        return out.strip() if ok else ""

    def _size(self, path: Path) -> int | None:
        result = self.runner.run(["du", "-skx", str(path)], timeout=300)
        if not result.ok or not result.stdout.strip():
            return None
        try:
            return int(result.stdout.split()[0]) * 1024
        except ValueError:
            return None

    # ---- scan

    def scan(self, repo: Path, *, fetch: bool = True, sizes: bool = True) -> RepoReport:
        """Gather facts about ``repo`` and attach a verdict to every branch/worktree."""
        report = RepoReport(path=repo, name=repo.name)

        ok, remote = self._git(repo, "remote", "get-url", "origin")
        report.gh_repo = parse_gh_repo(remote) if ok else None

        if fetch and ok:
            fetched = self.runner.run(
                ["git", "-C", str(repo), "fetch", "--prune", "--quiet", "origin"], timeout=180
            )
            if not fetched.ok:
                report.errors.append(f"git fetch failed: {fetched.stderr.strip()[:200]}")

        ok, head = self._git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        report.default_branch = (
            head.strip().removeprefix("origin/") if ok and head.strip() else ("main")
        )
        default_ref = f"origin/{report.default_branch}"

        prs: dict[str, PrInfo] = {}
        if report.gh_repo:
            index = self._pr_index(report.gh_repo)
            if index is None:
                report.errors.append("could not read pull requests from GitHub (gh)")
            else:
                prs = index

        ok, refs = self._git(
            repo,
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname)%09%(upstream:short)%09%(upstream:track)",
            "refs/heads",
        )
        if not ok:
            report.errors.append("could not list branches")
            return report
        for line in refs.splitlines():
            name, tip, upstream, track = ([*line.split("\t"), "", "", ""])[:4]
            report.branches.append(
                BranchInfo(
                    name=name,
                    tip=tip,
                    upstream=upstream,
                    upstream_gone="[gone]" in track,
                    pr=prs.get(name),
                )
            )
        tips = {b.name: b.tip for b in report.branches}

        # Worktrees first: a branch held by a worktree that is itself pruned is free.
        report.worktrees = self._worktrees(repo)
        for wt in report.worktrees:
            if wt.exists:
                ok, status = self._git(wt.path, "status", "--porcelain", "--untracked-files=normal")
                if not ok:
                    wt.dirty = ["<git status failed>"]
                else:
                    wt.dirty, wt.artifacts = split_status_paths(status, self.artifacts)
            wt.pr = prs.get(wt.branch) if wt.branch else None
            tip = tips.get(wt.branch, wt.head)
            resolved = wt.path.resolve() if wt.exists else wt.path
            wt.verdict, wt.reason = classify_worktree(
                wt,
                tip_in_pr_head=bool(wt.pr) and self._is_ancestor(repo, tip, wt.pr.head_oid),  # type: ignore[union-attr]
                tip_in_default=self._is_ancestor(repo, tip, default_ref),
                protected_path=any(
                    resolved == p or p in resolved.parents for p in self.protected_paths
                ),
                is_cwd=wt.exists and (self.cwd == resolved or resolved in self.cwd.parents),
            )
            if sizes and wt.exists and wt.verdict is not Verdict.KEEP:
                wt.size_bytes = self._size(wt.path)

        checked_out = {self._main_branch(repo)} | {
            w.branch for w in report.worktrees if w.branch and w.verdict is not Verdict.PRUNE
        }
        for branch in report.branches:
            branch.verdict, branch.reason = classify_branch(
                branch,
                default_branch=report.default_branch,
                protected=self.protected_branches,
                checked_out=checked_out,
                tip_in_pr_head=bool(branch.pr)
                and self._is_ancestor(repo, branch.tip, branch.pr.head_oid),  # type: ignore[union-attr]
                tip_in_default=self._is_ancestor(repo, branch.tip, default_ref),
            )
        return report

    # ---- act

    def prune(self, report: RepoReport, result: GitPruneResult) -> None:
        """Remove PRUNE worktrees, then PRUNE branches. Records into ``result``.

        Refuses outright for a repo whose scan had errors: a partial view (no PR
        data, failed fetch) is exactly when "merged" cannot be trusted.
        """
        if report.errors:
            result.failures.append(f"{report.name}: skipped — {'; '.join(report.errors)}")
            return
        repo = str(report.path)
        for wt in report.worktrees_with(Verdict.PRUNE):
            if not wt.exists:
                continue  # `worktree prune` below clears the record
            cmd = ["git", "-C", repo, "worktree", "remove"]
            if wt.needs_force:
                cmd.append("--force")  # only build artefacts are untracked (checked above)
            cmd.append(str(wt.path))
            done = self.runner.run(cmd, mutating=True, timeout=300)
            if done.ok:
                result.worktrees_removed.append(str(wt.path))
                result.bytes_reclaimed += wt.size_bytes or 0
                logger.info("git.worktree_removed", path=str(wt.path), reason=wt.reason)
            else:
                result.failures.append(f"{wt.path}: {done.stderr.strip()[:200]}")
        self.runner.run(["git", "-C", repo, "worktree", "prune"], mutating=True)
        for branch in report.branches_with(Verdict.PRUNE):
            done = self.runner.run(["git", "-C", repo, "branch", "-D", branch.name], mutating=True)
            if done.ok:
                result.branches_deleted.append(f"{report.name}:{branch.name}")
                logger.info("git.branch_deleted", repo=report.name, branch=branch.name)
            else:
                result.failures.append(f"{report.name}:{branch.name}: {done.stderr.strip()[:200]}")

    @staticmethod
    def collect_review(report: RepoReport, result: GitPruneResult) -> None:
        """Add the repo's REVIEW items to ``result.review``."""
        for wt in report.worktrees_with(Verdict.REVIEW):
            result.review.append(f"{report.name}: worktree {wt.path} — {wt.reason}")
        for b in report.branches_with(Verdict.REVIEW):
            result.review.append(f"{report.name}:{b.name} — {b.reason}")


# ------------------------------------------------------------------- state file


def new_result(*, dry_run: bool) -> GitPruneResult:
    """A fresh result stamped with the current UTC time."""
    return GitPruneResult(ran_at=datetime.now(UTC).isoformat(timespec="seconds"), dry_run=dry_run)


def save_result(result: GitPruneResult, path: Path) -> None:
    """Persist the last real run for `jt doctor`. Dry runs are never saved."""
    if result.dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2))


def load_result(path: Path) -> GitPruneResult | None:
    """The last saved run, or None when there is none (or it is unreadable)."""
    try:
        return GitPruneResult.model_validate_json(path.read_text())
    except OSError, ValueError:
        return None


def last_run_age_days(state_file: Path, *, now: datetime | None = None) -> float | None:
    """Days since the last recorded real prune, or None when there is none."""
    last = load_result(state_file)
    if last is None:
        return None
    try:
        ran = datetime.fromisoformat(last.ran_at)
    except ValueError:
        return None
    return ((now or datetime.now(UTC)) - ran).total_seconds() / 86400
