"""End-to-end CLI integration tests using Typer's CliRunner."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from janitor.main import app
from tests.conftest import FakeRunner

runner = CliRunner()


@pytest.fixture
def patched_runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """Force every command to use a single FakeRunner instance."""
    fake = FakeRunner()
    monkeypatch.setattr("janitor.main.ShellRunner", lambda **_: fake)
    return fake


pytestmark = pytest.mark.integration


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Janitor" in result.stdout


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Janitor" in result.stdout


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "housekeeping" in result.stdout.lower()


def test_doctor(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.system.which", lambda _: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Doctor" in result.stdout


def _doctor_with_disk(monkeypatch: pytest.MonkeyPatch, percent: int) -> str:
    """Run `jt doctor` with every tool healthy and the disk at `percent`."""
    from pathlib import Path

    from janitor.models.common import HealthStatus, ToolCheck
    from janitor.models.disk import DiskUsage

    monkeypatch.setattr(
        "janitor.services.system.SystemService.all_checks",
        lambda self: [
            ToolCheck(name="Python", available=True, status=HealthStatus.OK, version="3.14.0")
        ],
    )
    monkeypatch.setattr(
        "janitor.services.disk.DiskService.usage",
        lambda self, path=None: DiskUsage(
            path=Path("/"), total=100, used=percent, free=100 - percent
        ),
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    return result.stdout


def test_doctor_verdict_escalates_on_a_full_disk(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this covers: 97.9% used, summary said "All systems healthy".

    The row's threshold was a local variable the verdict never saw, so a
    scheduled check would have reported fine every day until the disk filled
    and ingest stopped (SB-861).
    """
    out = _doctor_with_disk(monkeypatch, 98)
    assert "Critical" in out
    assert "All systems healthy" not in out


def test_doctor_verdict_warns_on_a_filling_disk(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _doctor_with_disk(monkeypatch, 80)
    assert "attention" in out.lower()
    assert "All systems healthy" not in out


def test_doctor_still_reports_healthy_with_room_to_spare(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "All systems healthy" in _doctor_with_disk(monkeypatch, 50)


def test_doctor_tool_failure_still_dominates_a_healthy_disk(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No regression: the disk must not be able to downgrade a tool ERROR."""
    from pathlib import Path

    from janitor.models.common import HealthStatus, ToolCheck
    from janitor.models.disk import DiskUsage

    monkeypatch.setattr(
        "janitor.services.system.SystemService.all_checks",
        lambda self: [
            ToolCheck(name="Docker", available=False, status=HealthStatus.ERROR, detail="missing")
        ],
    )
    monkeypatch.setattr(
        "janitor.services.disk.DiskService.usage",
        lambda self, path=None: DiskUsage(path=Path("/"), total=100, used=10, free=90),
    )
    result = runner.invoke(app, ["doctor"])
    assert "Critical" in result.stdout


def test_docker_status(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.docker.which", lambda _: "/usr/bin/docker")
    patched_runner.stub(["docker", "info"], stdout="ok")
    patched_runner.stub(
        ["docker", "system", "df"],
        stdout=json.dumps(
            {
                "Type": "Images",
                "TotalCount": 1,
                "Active": 1,
                "Size": "1GB",
                "Reclaimable": "0B (0%)",
            }
        ),
    )
    result = runner.invoke(app, ["docker", "status"])
    assert result.exit_code == 0
    assert "Docker Disk Usage" in result.stdout


def test_docker_unavailable(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.docker.which", lambda _: None)
    result = runner.invoke(app, ["docker", "status"])
    assert result.exit_code == 1


def test_disk_usage() -> None:
    result = runner.invoke(app, ["disk", "usage", "/"])
    assert result.exit_code == 0
    assert "Disk Usage" in result.stdout


def test_brew_unavailable(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.brew.which", lambda _: None)
    result = runner.invoke(app, ["brew", "status"])
    assert result.exit_code == 1


def test_dry_run_prune(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.docker.which", lambda _: "/usr/bin/docker")
    patched_runner.stub(["docker", "info"], stdout="ok")
    patched_runner.stub(["docker", "system", "df"], stdout="{}")
    result = runner.invoke(app, ["--dry-run", "docker", "prune"])
    assert result.exit_code == 0
    assert "Dry-run" in result.stdout


def test_k3s_unavailable(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: None)
    result = runner.invoke(app, ["k3s", "status"])
    assert result.exit_code == 1


def test_logs_size_empty(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JANITOR_LOGS__PATHS", f'["{tmp_path}"]')
    result = runner.invoke(app, ["logs", "size"])
    assert result.exit_code == 0
