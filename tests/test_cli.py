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
