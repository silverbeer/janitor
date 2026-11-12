"""`jt k3s maintain` and `jt k3s schedule` through the CLI (SB-1632)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from janitor.main import app
from janitor.models.k3s import K3sMaintainResult
from janitor.services.k3s import load_maintain_result, save_maintain_result
from tests.conftest import FakeRunner

runner = CliRunner()
pytestmark = pytest.mark.integration

VM = ["rdctl", "shell", "--", "sudo"]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    f = FakeRunner()
    monkeypatch.setattr("janitor.main.ShellRunner", lambda **_: f)
    monkeypatch.setattr("janitor.services.k3s.which", lambda name: f"/bin/{name}")
    f.stub([*VM, "k3s", "crictl", "images"], stdout=json.dumps({"images": []}))
    f.stub([*VM, "k3s", "crictl", "ps"], stdout=json.dumps({"containers": []}))
    f.stub(["kubectl", "--context", "rancher-desktop", "get"], stdout=json.dumps({"items": []}))
    f.stub([*VM, "buildctl", "--addr"], stdout=json.dumps([{"size": 2048, "inUse": False}]))
    f.stub(
        [*VM, "buildctl", "--addr", "unix:///run/buildkit/buildkitd.sock", "prune"],
        stdout='{"size": 2048}\n',
    )
    f.stub([*VM, "fstrim"], stdout="/mnt/data: 3 GiB trimmed")
    return f


def _state() -> Path:
    return Path(os.environ["JANITOR_K3S__STATE_FILE"])


def test_maintain_records_the_run(fake: FakeRunner) -> None:
    result = runner.invoke(app, ["--yes", "k3s", "maintain"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    assert "Freed" in result.stdout and "2.0 KB build cache" in result.stdout
    assert "3 GiB" in result.stdout
    last = load_maintain_result(_state())
    assert last is not None and last.build_cache_freed == 2048


def test_maintain_dry_run_saves_nothing(fake: FakeRunner) -> None:
    fake.dry_run = True
    result = runner.invoke(app, ["--dry-run", "k3s", "maintain"])
    assert result.exit_code == 0
    assert "Would free" in result.stdout
    assert not _state().exists()


def test_maintain_failure_exits_nonzero_and_is_recorded(fake: FakeRunner) -> None:
    fake.stub([*VM, "fstrim", "-v"], returncode=1, stderr="busy")
    result = runner.invoke(app, ["--yes", "k3s", "maintain"])
    assert result.exit_code == 1
    last = load_maintain_result(_state())
    assert last is not None and last.failures


def test_maintain_declined(fake: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.commands.k3s.confirm", lambda *_a, **_k: False)
    result = runner.invoke(app, ["k3s", "maintain"])
    assert "Aborted" in result.stdout
    assert not any("buildctl" in c and "prune" in c for c in fake.calls)


def test_schedule_install_status_uninstall(
    fake: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("janitor.commands.k3s.which", lambda _: "/bin/jt")
    monkeypatch.setattr("janitor.commands.k3s.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    env = {"JANITOR_K3S__LOG_FILE": str(tmp_path / "l.log"), "COLUMNS": "200"}
    result = runner.invoke(app, ["k3s", "schedule", "install"], env=env)
    assert result.exit_code == 0, result.stdout
    assert "weekly on Sunday at 04:00" in result.stdout
    plist = tmp_path / "Library" / "LaunchAgents" / "com.silverbeer.janitor.k3s-maintain.plist"
    assert b"maintain" in plist.read_bytes()

    result = runner.invoke(app, ["k3s", "schedule", "status"], env=env)
    assert "loaded" in result.stdout and "No recorded run" in result.stdout
    save_maintain_result(K3sMaintainResult(ran_at="2026-11-15T04:00:00+00:00"), _state())
    result = runner.invoke(app, ["k3s", "schedule", "status"], env=env)
    assert "Last run 2026-11-15" in result.stdout

    result = runner.invoke(app, ["k3s", "schedule", "uninstall"])
    assert "Removed" in result.stdout and not plist.exists()
    result = runner.invoke(app, ["k3s", "schedule", "uninstall"])
    assert "Nothing was installed" in result.stdout


def test_schedule_install_refused_by_launchctl(
    fake: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("janitor.commands.k3s.which", lambda _: "/bin/jt")
    monkeypatch.setattr("janitor.commands.k3s.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake.stub(["launchctl", "bootstrap"], returncode=5)
    result = runner.invoke(
        app, ["k3s", "schedule", "install"], env={"JANITOR_K3S__LOG_FILE": str(tmp_path / "l")}
    )
    assert result.exit_code == 1


def test_schedule_install_needs_jt(fake: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.commands.k3s.which", lambda _: None)
    monkeypatch.setattr("janitor.commands.k3s.sys.platform", "darwin")
    assert runner.invoke(app, ["k3s", "schedule", "install"]).exit_code == 1


def test_schedule_install_is_macos_only(fake: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.commands.k3s.sys.platform", "linux")
    assert runner.invoke(app, ["k3s", "schedule", "install"]).exit_code == 1


# --- doctor -------------------------------------------------------------------


def _doctor(monkeypatch: pytest.MonkeyPatch) -> str:
    from tests.test_cli import _doctor_with_disk

    return _doctor_with_disk(monkeypatch, 10)


def test_doctor_upkeep_unknown_before_first_run(
    fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _doctor(monkeypatch)
    assert "k3s upkeep" in out and "no recorded run" in out
    assert "All systems healthy" in out


def test_doctor_upkeep_ok_after_recent_run(
    fake: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    save_maintain_result(K3sMaintainResult(ran_at=ran, build_cache_freed=2048), _state())
    out = _doctor(monkeypatch)
    assert "build cache freed" in out and "All systems healthy" in out


@pytest.mark.parametrize(
    ("days", "failures"),
    [(15, []), (1, ["trim: busy"])],
)
def test_doctor_upkeep_warns_when_stale_or_failed(
    fake: FakeRunner, monkeypatch: pytest.MonkeyPatch, days: int, failures: list[str]
) -> None:
    ran = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    save_maintain_result(K3sMaintainResult(ran_at=ran, failures=failures), _state())
    out = _doctor(monkeypatch)
    assert "need attention" in out
