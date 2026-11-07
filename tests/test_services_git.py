"""`jt git` decisions and the service around them (SB-1580).

The classifier tests are the contract: they pin, case by case, that nothing
which could hold unsaved work is ever marked PRUNE.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from janitor.models.git import BranchInfo, GitPruneResult, PrInfo, RepoReport, Verdict, WorktreeInfo
from janitor.services.git import (
    GitService,
    classify_branch,
    classify_worktree,
    last_run_age_days,
    load_result,
    new_result,
    parse_gh_repo,
    save_result,
    split_status_paths,
)
from janitor.services.schedule import LaunchAgent, build_plist
from tests.conftest import FakeRunner

_real_is_dir = Path.is_dir

MERGED = PrInfo(number=7, state="MERGED", head_oid="abc")
OPEN = PrInfo(number=8, state="OPEN", head_oid="def")
CLOSED = PrInfo(number=9, state="CLOSED", head_oid="ghi")


def _branch(**kw: object) -> BranchInfo:
    return BranchInfo(**{"name": "feat", "tip": "t1", **kw})  # type: ignore[arg-type]


def _cb(branch: BranchInfo, *, pr_head: bool = False, default: bool = False, out=()) -> tuple:  # type: ignore[no-untyped-def]
    return classify_branch(
        branch,
        default_branch="main",
        protected={"main", "master", "develop"},
        checked_out=set(out),
        tip_in_pr_head=pr_head,
        tip_in_default=default,
    )


class TestClassifyBranch:
    def test_merged_pr_with_nothing_new_is_pruned(self) -> None:
        assert _cb(_branch(pr=MERGED), pr_head=True)[0] is Verdict.PRUNE

    def test_squash_merged_tip_on_main_is_pruned(self) -> None:
        assert _cb(_branch(pr=MERGED), default=True)[0] is Verdict.PRUNE

    def test_commits_after_merge_need_review_not_deletion(self) -> None:
        verdict, reason = _cb(_branch(pr=MERGED))
        assert verdict is Verdict.REVIEW
        assert "newer commits" in reason

    def test_open_pr_is_kept(self) -> None:
        assert _cb(_branch(pr=OPEN), pr_head=True, default=True)[0] is Verdict.KEEP

    def test_closed_unmerged_pr_needs_review(self) -> None:
        verdict, reason = _cb(_branch(pr=CLOSED), pr_head=True)
        assert verdict is Verdict.REVIEW
        assert "closed without merging" in reason

    def test_closed_pr_whose_commits_landed_anyway_is_pruned(self) -> None:
        assert _cb(_branch(pr=CLOSED), default=True)[0] is Verdict.PRUNE

    def test_protected_and_default_branches_are_never_touched(self) -> None:
        for name in ("main", "master", "develop"):
            assert _cb(_branch(name=name, pr=MERGED), pr_head=True)[0] is Verdict.KEEP

    def test_checked_out_branch_is_kept(self) -> None:
        verdict, reason = _cb(_branch(pr=MERGED), pr_head=True, out={"feat"})
        assert verdict is Verdict.KEEP
        assert reason == "checked out"

    def test_new_local_branch_off_main_is_kept_not_pruned(self) -> None:
        # Freshly created, no commits yet: its tip IS on main. Must not vanish.
        assert _cb(_branch(), default=True)[0] is Verdict.KEEP

    def test_pushed_branch_without_pr_is_kept(self) -> None:
        assert _cb(_branch(upstream="origin/feat"), default=True)[0] is Verdict.KEEP

    def test_remote_deleted_and_on_main_is_pruned(self) -> None:
        assert _cb(_branch(upstream_gone=True), default=True)[0] is Verdict.PRUNE

    def test_remote_deleted_but_commits_not_on_main_needs_review(self) -> None:
        assert _cb(_branch(upstream_gone=True))[0] is Verdict.REVIEW


def _cw(
    wt: WorktreeInfo,
    *,
    pr_head: bool = True,
    default: bool = False,
    protected: bool = False,
    cwd: bool = False,
) -> tuple:  # type: ignore[no-untyped-def]
    return classify_worktree(
        wt, tip_in_pr_head=pr_head, tip_in_default=default, protected_path=protected, is_cwd=cwd
    )


def _wt(**kw: object) -> WorktreeInfo:
    return WorktreeInfo(**{"path": Path("/w/feat"), "branch": "feat", "pr": MERGED, **kw})  # type: ignore[arg-type]


class TestClassifyWorktree:
    def test_clean_merged_worktree_is_pruned(self) -> None:
        assert _cw(_wt())[0] is Verdict.PRUNE

    def test_only_build_artefacts_still_counts_as_clean(self) -> None:
        wt = _wt(artifacts=["backend/.venv/"])
        assert _cw(wt)[0] is Verdict.PRUNE
        assert wt.needs_force

    def test_uncommitted_work_needs_review(self) -> None:
        verdict, reason = _cw(_wt(dirty=["src/app.py"]))
        assert verdict is Verdict.REVIEW
        assert "1 uncommitted" in reason

    def test_locked_is_kept(self) -> None:
        assert _cw(_wt(locked=True))[0] is Verdict.KEEP

    def test_detached_is_kept(self) -> None:
        assert _cw(_wt(branch=""))[0] is Verdict.KEEP

    def test_current_directory_is_kept(self) -> None:
        assert _cw(_wt(), cwd=True)[1] == "current directory"

    def test_protected_path_is_kept(self) -> None:
        assert _cw(_wt(), protected=True)[1] == "protected path"

    def test_missing_directory_is_a_stale_record(self) -> None:
        assert _cw(_wt(exists=False, pr=None))[0] is Verdict.PRUNE

    def test_open_pr_kept_closed_reviewed_no_pr_kept(self) -> None:
        assert _cw(_wt(pr=OPEN))[0] is Verdict.KEEP
        assert _cw(_wt(pr=CLOSED))[0] is Verdict.REVIEW
        assert _cw(_wt(pr=None))[0] is Verdict.KEEP

    def test_commits_after_merge_in_worktree_need_review(self) -> None:
        assert _cw(_wt(), pr_head=False)[0] is Verdict.REVIEW


class TestPureHelpers:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://github.com/silverbeer/janitor.git", "silverbeer/janitor"),
            ("git@github.com:silverbeer/janitor.git", "silverbeer/janitor"),
            ("https://github.com/silverbeer/janitor", "silverbeer/janitor"),
            ("https://gitlab.com/x/y.git", None),
        ],
    )
    def test_parse_gh_repo(self, url: str, expected: str | None) -> None:
        assert parse_gh_repo(url) == expected

    def test_split_status_paths(self) -> None:
        porcelain = "\n".join(
            [
                "?? backend/.venv/",
                "?? frontend/node_modules",
                " M src/app.py",
                "?? notes.md",
                "R  old.py -> new.py",
            ]
        )
        dirty, junk = split_status_paths(porcelain)
        assert junk == ["backend/.venv/", "frontend/node_modules"]
        assert dirty == ["src/app.py", "notes.md", "new.py"]

    def test_a_tracked_change_inside_an_artefact_name_is_still_work(self) -> None:
        # Only *untracked* artefacts are ignored; a modified tracked file under
        # build/ is somebody's edit.
        dirty, junk = split_status_paths(" M build/config.py")
        assert dirty == ["build/config.py"]
        assert junk == []


# ------------------------------------------------------------------- service

REPO = Path("/r/app")


def _stub_repo(fake: FakeRunner, *, prs: list[dict[str, object]], gh_ok: bool = True) -> None:
    r = str(REPO)
    fake.stub(["git", "-C", r, "remote", "get-url", "origin"], stdout="git@github.com:o/app.git\n")
    fake.stub(["git", "-C", r, "symbolic-ref"], stdout="origin/main\n")
    fake.stub(["git", "-C", r, "branch", "--show-current"], stdout="main\n")
    fake.stub(
        ["git", "-C", r, "for-each-ref"],
        stdout="\n".join(
            [
                "main\tm0\torigin/main\t",
                "done\tt1\torigin/done\t[gone]",
                "wip\tt2\torigin/wip\t",
                "after\tt3\t\t",
            ]
        ),
    )
    fake.stub(
        ["git", "-C", r, "worktree", "list", "--porcelain"],
        stdout=(
            f"worktree {r}\nHEAD m0\nbranch refs/heads/main\n\n"
            "worktree /w/done\nHEAD t1\nbranch refs/heads/done\n\n"
            "worktree /w/locked\nHEAD t9\nbranch refs/heads/wip\nlocked\n"
        ),
    )
    fake.stub(["git", "-C", "/w/done", "status"], stdout="?? .venv/\n")
    fake.stub(["du", "-skx"], stdout="2048\t/w/done\n")
    fake.stub(
        ["gh", "pr", "list"],
        stdout=json.dumps(prs),
        returncode=0 if gh_ok else 1,
    )
    # merge-base: t1 is in PR head; everything else is not an ancestor.
    fake.stub(["git", "-C", r, "merge-base"], returncode=1)
    fake.stub(["git", "-C", r, "merge-base", "--is-ancestor", "t1", "h1"], returncode=0)


PRS = [
    {"number": 1, "state": "MERGED", "headRefName": "done", "headRefOid": "h1"},
    {"number": 2, "state": "OPEN", "headRefName": "wip", "headRefOid": "h2"},
    {"number": 3, "state": "MERGED", "headRefName": "after", "headRefOid": "h3"},
    {"number": 0, "state": "CLOSED", "headRefName": "done", "headRefOid": "old"},
]


@pytest.fixture
def scanned(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeRunner, RepoReport]:
    monkeypatch.setattr(
        Path, "is_dir", lambda self: str(self) in {"/w/done", "/w/locked"} or _real_is_dir(self)
    )
    fake = FakeRunner()
    _stub_repo(fake, prs=PRS)
    report = GitService(fake, cwd=Path("/elsewhere")).scan(REPO)
    return fake, report


class TestScan:
    def test_reads_github_and_default_branch(self, scanned: tuple[FakeRunner, RepoReport]) -> None:
        _, report = scanned
        assert report.gh_repo == "o/app"
        assert report.default_branch == "main"
        assert report.errors == []

    def test_verdicts(self, scanned: tuple[FakeRunner, RepoReport]) -> None:
        _, report = scanned
        by_name = {b.name: b for b in report.branches}
        assert by_name["main"].verdict is Verdict.KEEP
        # Held by a worktree that is itself pruned → free to delete.
        assert by_name["done"].verdict is Verdict.PRUNE
        assert by_name["done"].pr is not None and by_name["done"].pr.number == 1
        assert by_name["wip"].verdict is Verdict.KEEP  # open PR (and locked worktree)
        assert by_name["after"].verdict is Verdict.REVIEW  # merged, tip not in PR head

    def test_worktrees(self, scanned: tuple[FakeRunner, RepoReport]) -> None:
        _, report = scanned
        done, locked = report.worktrees
        assert done.verdict is Verdict.PRUNE
        assert done.artifacts == [".venv/"]
        assert done.size_bytes == 2048 * 1024
        assert locked.verdict is Verdict.KEEP and locked.reason == "locked"
        assert report.reclaimable_bytes == 2048 * 1024

    def test_fetch_prunes_remote_refs_first(self, scanned: tuple[FakeRunner, RepoReport]) -> None:
        fake, _ = scanned
        assert ["git", "-C", str(REPO), "fetch", "--prune", "--quiet", "origin"] in fake.calls

    def test_gh_failure_is_an_error_and_blocks_prune(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "is_dir", lambda self: True)
        fake = FakeRunner()
        _stub_repo(fake, prs=[], gh_ok=False)
        service = GitService(fake, cwd=Path("/elsewhere"))
        report = service.scan(REPO, sizes=False)
        assert any("pull requests" in e for e in report.errors)
        result = new_result(dry_run=False)
        service.prune(report, result)
        assert result.failures and "skipped" in result.failures[0]
        assert not any(c[3:5] == ["branch", "-D"] for c in fake.calls)

    def test_failed_fetch_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "is_dir", lambda self: True)
        fake = FakeRunner()
        _stub_repo(fake, prs=PRS)
        fake.stub(["git", "-C", str(REPO), "fetch"], returncode=1, stderr="offline")
        report = GitService(fake, cwd=Path("/elsewhere")).scan(REPO, sizes=False)
        assert any("fetch" in e for e in report.errors)


class TestPrune:
    def test_removes_worktree_with_force_then_branch(
        self, scanned: tuple[FakeRunner, RepoReport]
    ) -> None:
        fake, report = scanned
        result = new_result(dry_run=False)
        GitService(fake).prune(report, result)
        r = str(REPO)
        assert ["git", "-C", r, "worktree", "remove", "--force", "/w/done"] in fake.calls
        assert ["git", "-C", r, "branch", "-D", "done"] in fake.calls
        assert ["git", "-C", r, "branch", "-D", "after"] not in fake.calls  # REVIEW
        assert ["git", "-C", r, "branch", "-D", "wip"] not in fake.calls  # KEEP
        assert result.branches_deleted == ["app:done"]
        assert result.worktrees_removed == ["/w/done"]
        remove_at = fake.calls.index(["git", "-C", r, "worktree", "remove", "--force", "/w/done"])
        delete_at = fake.calls.index(["git", "-C", r, "branch", "-D", "done"])
        assert remove_at < delete_at  # a checked-out branch cannot be deleted

    def test_dry_run_records_but_executes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            Path, "is_dir", lambda self: str(self) in {"/w/done", "/w/locked"} or _real_is_dir(self)
        )
        fake = FakeRunner(dry_run=True)
        _stub_repo(fake, prs=PRS)
        service = GitService(fake, cwd=Path("/elsewhere"))
        report = service.scan(REPO)
        result = new_result(dry_run=True)
        fake.calls.clear()
        service.prune(report, result)
        assert result.branches_deleted == ["app:done"]  # FakeRunner skips mutating calls

    def test_failure_is_reported(self, scanned: tuple[FakeRunner, RepoReport]) -> None:
        fake, report = scanned
        fake.stub(["git", "-C", str(REPO), "branch", "-D"], returncode=1, stderr="nope")
        result = new_result(dry_run=False)
        GitService(fake).prune(report, result)
        assert any("nope" in f for f in result.failures)

    def test_collect_review(self, scanned: tuple[FakeRunner, RepoReport]) -> None:
        _, report = scanned
        result = new_result(dry_run=False)
        GitService.collect_review(report, result)
        assert result.review == ["app:after — PR #3 merged, but local has newer commits"]


class TestDiscovery:
    def test_finds_main_checkouts_not_worktrees(self, tmp_path: Path) -> None:
        (tmp_path / "a" / ".git").mkdir(parents=True)
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / ".git").write_text("gitdir: elsewhere")  # a linked worktree
        (tmp_path / "c" / ".git").mkdir(parents=True)
        found = GitService.discover([tmp_path, tmp_path / "missing"], exclude=["c"])
        assert found == [tmp_path / "a"]

    def test_main_checkout_from_inside_a_worktree(self) -> None:
        fake = FakeRunner()
        fake.stub(["git", "-C", "/w/x", "rev-parse"], stdout="/r/app/.git\n")
        assert GitService(fake).main_checkout(Path("/w/x")) == Path("/r/app")
        fake.stub(["git", "-C", "/nope", "rev-parse"], returncode=128)
        assert GitService(fake).main_checkout(Path("/nope")) is None


class TestStateFile:
    def test_round_trip_and_age(self, tmp_path: Path) -> None:
        path = tmp_path / "s" / "git-prune.json"
        assert load_result(path) is None
        assert last_run_age_days(path) is None
        then = (datetime.now(UTC) - timedelta(days=2)).isoformat(timespec="seconds")
        save_result(GitPruneResult(ran_at=then, branches_deleted=["a:b"]), path)
        assert load_result(path).branches_deleted == ["a:b"]  # type: ignore[union-attr]
        age = last_run_age_days(path)
        assert age is not None and 1.9 < age < 2.1

    def test_dry_runs_are_never_saved(self, tmp_path: Path) -> None:
        path = tmp_path / "git-prune.json"
        save_result(new_result(dry_run=True), path)
        assert not path.exists()

    def test_bad_timestamp_has_no_age(self, tmp_path: Path) -> None:
        path = tmp_path / "git-prune.json"
        path.write_text(GitPruneResult(ran_at="yesterday").model_dump_json())
        assert last_run_age_days(path) is None


class TestSchedule:
    def test_plist(self, tmp_path: Path) -> None:
        import plistlib

        data = plistlib.loads(
            build_plist(
                label="x.y",
                program=["/bin/jt", "--yes", "git", "prune"],
                hour=3,
                minute=30,
                log_file=tmp_path / "l.log",
            )
        )
        assert data["Label"] == "x.y"
        assert data["ProgramArguments"][-2:] == ["git", "prune"]
        assert data["StartCalendarInterval"] == {"Hour": 3, "Minute": 30}
        assert "/opt/homebrew/bin" in data["EnvironmentVariables"]["PATH"]

    def test_install_status_uninstall(self, tmp_path: Path) -> None:
        fake = FakeRunner()
        agent = LaunchAgent(fake, "x.y", agents_dir=tmp_path / "agents")
        assert agent.install(b"<plist/>", log_file=tmp_path / "logs" / "l.log")
        assert agent.plist_path.read_bytes() == b"<plist/>"
        assert any(c[:2] == ["launchctl", "bootstrap"] for c in fake.calls)
        assert agent.loaded()
        assert agent.uninstall()
        assert not agent.plist_path.exists()
        assert not agent.uninstall()

    def test_install_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        agent = LaunchAgent(FakeRunner(dry_run=True), "x.y", agents_dir=tmp_path / "agents")
        assert agent.install(b"<plist/>", log_file=tmp_path / "l.log")
        assert not agent.plist_path.exists()
