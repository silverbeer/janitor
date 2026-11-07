"""`jt git` through the CLI (SB-1580)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from janitor.main import app
from tests.conftest import FakeRunner
from tests.test_services_git import PRS, REPO, _stub_repo

_real_is_dir = Path.is_dir

runner = CliRunner()
pytestmark = pytest.mark.integration


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    f = FakeRunner()
    monkeypatch.setattr("janitor.main.ShellRunner", lambda **_: f)
    monkeypatch.setattr(
        Path, "is_dir", lambda self: str(self) in {"/w/done", "/w/locked"} or _real_is_dir(self)
    )
    _stub_repo(f, prs=PRS)
    f.stub(["git", "-C", ".", "rev-parse"], stdout=f"{REPO}/.git\n")
    return f


def test_audit_is_read_only(fake: FakeRunner) -> None:
    result = runner.invoke(app, ["git", "audit", "--repo", "."], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    assert "Git hygiene" in result.stdout
    assert "PR #1 merged" in result.stdout
    assert "newer commits" in result.stdout
    assert not any("-D" in c or "remove" in c for c in fake.calls)


def test_audit_all_lists_keep(fake: FakeRunner) -> None:
    result = runner.invoke(app, ["git", "audit", "--repo", ".", "--all"], env={"COLUMNS": "200"})
    assert "locked" in result.stdout


def test_prune_yes_deletes_only_prune_items(fake: FakeRunner) -> None:
    result = runner.invoke(app, ["--yes", "git", "prune", "--repo", "."], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    assert ["git", "-C", str(REPO), "branch", "-D", "done"] in fake.calls
    assert ["git", "-C", str(REPO), "branch", "-D", "after"] not in fake.calls
    assert "need a human" in result.stdout


def test_prune_dry_run_deletes_nothing(fake: FakeRunner) -> None:
    result = runner.invoke(app, ["--dry-run", "git", "prune", "--repo", "."])
    assert result.exit_code == 0
    assert "Dry-run" in result.stdout


def test_prune_declined_aborts(fake: FakeRunner) -> None:
    result = runner.invoke(app, ["git", "prune", "--repo", "."], input="n\n")
    assert "Aborted" in result.stdout
    assert ["git", "-C", str(REPO), "branch", "-D", "done"] not in fake.calls


def test_not_a_repo_exits_1(fake: FakeRunner) -> None:
    fake.stub(["git", "-C", "/nope", "rev-parse"], returncode=128)
    result = runner.invoke(app, ["git", "audit", "--repo", "/nope"])
    assert result.exit_code == 1


def test_no_repos_found(fake: FakeRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["git", "audit"], env={"JANITOR_GIT__ROOTS": f'["{tmp_path}"]'})
    assert "No git repositories" in result.stdout


def test_nothing_to_prune(monkeypatch: pytest.MonkeyPatch) -> None:
    f = FakeRunner()
    monkeypatch.setattr("janitor.main.ShellRunner", lambda **_: f)
    f.stub(["git", "-C", ".", "rev-parse"], stdout=f"{REPO}/.git\n")
    f.stub(["git", "-C", str(REPO), "remote"], returncode=1)
    f.stub(["git", "-C", str(REPO), "for-each-ref"], stdout="main\tm0\torigin/main\t")
    f.stub(["git", "-C", str(REPO), "branch", "--show-current"], stdout="main\n")
    result = runner.invoke(app, ["--yes", "git", "prune", "--repo", "."])
    assert "Nothing to prune" in result.stdout


def test_schedule_status_without_run(fake: FakeRunner) -> None:
    fake.stub(["launchctl", "print"], returncode=113)
    result = runner.invoke(app, ["git", "schedule", "status"])
    assert "not loaded" in result.stdout
    assert "No recorded run" in result.stdout


def test_schedule_install_and_uninstall(
    fake: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("janitor.commands.git.which", lambda _: "/bin/jt")
    monkeypatch.setattr("janitor.commands.git.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = runner.invoke(
        app, ["git", "schedule", "install"], env={"JANITOR_GIT__LOG_FILE": str(tmp_path / "l.log")}
    )
    assert result.exit_code == 0, result.stdout
    assert "Scheduled daily at 03:30" in result.stdout
    result = runner.invoke(app, ["git", "schedule", "uninstall"])
    assert "Removed" in result.stdout


def test_schedule_install_needs_jt(fake: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.commands.git.which", lambda _: None)
    monkeypatch.setattr("janitor.commands.git.sys.platform", "darwin")
    assert runner.invoke(app, ["git", "schedule", "install"]).exit_code == 1


def test_schedule_install_is_macos_only(fake: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.commands.git.sys.platform", "linux")
    assert runner.invoke(app, ["git", "schedule", "install"]).exit_code == 1


def test_doctor_shows_git_hygiene(fake: FakeRunner) -> None:
    result = runner.invoke(app, ["doctor"], env={"COLUMNS": "200"})
    assert "Git hygiene" in result.stdout
    assert "no recorded prune" in result.stdout
