"""Git housekeeping models — what `jt git` found and what it decided (SB-1580).

Every branch and worktree gets exactly one :class:`Verdict`. ``PRUNE`` is the
only one that ever leads to a deletion; ``REVIEW`` means "a human should look",
and ``KEEP`` means "nothing to do". The reason travels with the verdict so the
report says *why*, not just *what*.
"""

from __future__ import annotations

import enum
from pathlib import Path

from pydantic import BaseModel, Field

__all__ = [
    "BranchInfo",
    "GitPruneResult",
    "PrInfo",
    "RepoReport",
    "Verdict",
    "WorktreeInfo",
]


class Verdict(enum.StrEnum):
    """What `jt git prune` will do with a branch or worktree."""

    PRUNE = "prune"  # safe to delete: merged, nothing local would be lost
    REVIEW = "review"  # possibly stale, but deleting could lose work — a human decides
    KEEP = "keep"  # in use or still open

    @property
    def style(self) -> str:
        """Rich style name for this verdict."""
        return {Verdict.PRUNE: "ok", Verdict.REVIEW: "warn", Verdict.KEEP: "muted"}[self]


class PrInfo(BaseModel):
    """The most recent pull request whose head was a given branch."""

    number: int
    state: str  # OPEN | MERGED | CLOSED
    head_oid: str = ""


class BranchInfo(BaseModel):
    """A local branch and the decision about it."""

    name: str
    tip: str
    upstream: str = ""
    upstream_gone: bool = False
    pr: PrInfo | None = None
    verdict: Verdict = Verdict.KEEP
    reason: str = ""


class WorktreeInfo(BaseModel):
    """A linked worktree (never the main checkout) and the decision about it."""

    path: Path
    branch: str = ""  # empty when detached
    head: str = ""
    locked: bool = False
    exists: bool = True
    #: Untracked/modified paths that are real work (not build artefacts).
    dirty: list[str] = Field(default_factory=list)
    #: Untracked paths ignored as build artefacts (.venv, node_modules, ...).
    artifacts: list[str] = Field(default_factory=list)
    size_bytes: int | None = None
    pr: PrInfo | None = None
    verdict: Verdict = Verdict.KEEP
    reason: str = ""

    @property
    def needs_force(self) -> bool:
        """`git worktree remove` refuses untracked files without --force."""
        return bool(self.artifacts)


class RepoReport(BaseModel):
    """Everything `jt git` knows about one repository."""

    path: Path
    name: str
    gh_repo: str | None = None  # owner/name when the origin is on GitHub
    default_branch: str = "main"
    branches: list[BranchInfo] = Field(default_factory=list)
    worktrees: list[WorktreeInfo] = Field(default_factory=list)
    #: Problems that stopped part of the scan. A repo with errors is never pruned.
    errors: list[str] = Field(default_factory=list)

    def branches_with(self, verdict: Verdict) -> list[BranchInfo]:
        """Branches carrying ``verdict``."""
        return [b for b in self.branches if b.verdict is verdict]

    def worktrees_with(self, verdict: Verdict) -> list[WorktreeInfo]:
        """Worktrees carrying ``verdict``."""
        return [w for w in self.worktrees if w.verdict is verdict]

    @property
    def reclaimable_bytes(self) -> int:
        """Disk freed by removing the PRUNE worktrees (branches are ~free)."""
        return sum(w.size_bytes or 0 for w in self.worktrees_with(Verdict.PRUNE))


class GitPruneResult(BaseModel):
    """Outcome of one `jt git prune` run, also persisted for `jt doctor`."""

    ran_at: str
    dry_run: bool = False
    repos: int = 0
    branches_deleted: list[str] = Field(default_factory=list)  # "repo:branch"
    worktrees_removed: list[str] = Field(default_factory=list)  # paths
    bytes_reclaimed: int = 0
    review: list[str] = Field(default_factory=list)  # "repo:thing — reason"
    failures: list[str] = Field(default_factory=list)
