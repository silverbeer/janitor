"""Broader CLI coverage for command rendering and cleanup paths."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from janitor.main import app
from tests.conftest import FakeRunner

runner = CliRunner()
pytestmark = pytest.mark.integration


@pytest.fixture
def patched_runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    fake = FakeRunner()
    monkeypatch.setattr("janitor.main.ShellRunner", lambda **_: fake)
    return fake


def _docker_available(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.docker.which", lambda _: "/usr/bin/docker")
    patched_runner.stub(["docker", "info"], stdout="ok")


# --- docker ---------------------------------------------------------------


def test_docker_images(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_available(patched_runner, monkeypatch)
    patched_runner.stub(
        ["docker", "images"],
        stdout=json.dumps({"ID": "a", "Repository": "nginx", "Tag": "latest", "Size": "100MB"}),
    )
    result = runner.invoke(app, ["docker", "images"])
    assert result.exit_code == 0
    assert "nginx" in result.stdout


def test_docker_volumes(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_available(patched_runner, monkeypatch)
    patched_runner.stub(["docker", "volume", "ls", "--filter"], stdout="")
    patched_runner.stub(
        ["docker", "volume", "ls", "--format"],
        stdout=json.dumps({"Name": "data", "Driver": "local"}),
    )
    result = runner.invoke(app, ["docker", "volumes"])
    assert result.exit_code == 0
    assert "data" in result.stdout


def test_docker_reclaim(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_available(patched_runner, monkeypatch)
    patched_runner.stub(
        ["docker", "system", "df"],
        stdout=json.dumps(
            {
                "Type": "Images",
                "TotalCount": 1,
                "Active": 0,
                "Size": "1GB",
                "Reclaimable": "1GB (100%)",
            }
        ),
    )
    result = runner.invoke(app, ["docker", "reclaim"])
    assert result.exit_code == 0
    assert "Reclaimable" in result.stdout


def test_docker_prune_yes(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_available(patched_runner, monkeypatch)
    patched_runner.stub(["docker", "system", "df"], stdout="{}")
    result = runner.invoke(app, ["--yes", "docker", "prune", "--aggressive"])
    assert result.exit_code == 0
    assert "complete" in result.stdout.lower()


def test_docker_prune_abort(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_available(patched_runner, monkeypatch)
    patched_runner.stub(["docker", "system", "df"], stdout="{}")
    result = runner.invoke(app, ["docker", "prune"], input="n\n")
    assert result.exit_code == 0
    assert "Aborted" in result.stdout


# --- brew -----------------------------------------------------------------


def _brew_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.brew.which", lambda _: "/opt/homebrew/bin/brew")


def test_brew_status_outdated(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _brew_available(monkeypatch)
    patched_runner.stub(["brew", "--prefix"], stdout="/opt/homebrew")
    patched_runner.stub(
        ["brew", "outdated"],
        stdout=json.dumps(
            {
                "formulae": [
                    {"name": "jq", "installed_versions": ["1.6"], "current_version": "1.7"}
                ],
                "casks": [],
            }
        ),
    )
    result = runner.invoke(app, ["brew", "status"])
    assert result.exit_code == 0
    assert "jq" in result.stdout


def test_brew_upgrade_yes(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _brew_available(monkeypatch)
    patched_runner.stub(["brew", "--prefix"], stdout="/opt/homebrew")
    patched_runner.stub(
        ["brew", "outdated"],
        stdout=json.dumps(
            {
                "formulae": [
                    {"name": "jq", "installed_versions": ["1.6"], "current_version": "1.7"}
                ],
                "casks": [],
            }
        ),
    )
    patched_runner.stub(["brew", "upgrade"], stdout="done")
    result = runner.invoke(app, ["--yes", "brew", "upgrade"])
    assert result.exit_code == 0


def test_brew_upgrade_nothing(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _brew_available(monkeypatch)
    patched_runner.stub(["brew", "--prefix"], stdout="/opt/homebrew")
    patched_runner.stub(["brew", "outdated"], stdout='{"formulae": [], "casks": []}')
    result = runner.invoke(app, ["brew", "upgrade"])
    assert result.exit_code == 0
    assert "Nothing" in result.stdout


def test_brew_cleanup_yes(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _brew_available(monkeypatch)
    patched_runner.stub(["brew", "cleanup"], stdout="cleaned")
    result = runner.invoke(app, ["--yes", "brew", "cleanup"])
    assert result.exit_code == 0


# --- disk -----------------------------------------------------------------


def test_disk_largest_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "big.bin").write_bytes(b"x" * 5000)
    result = runner.invoke(app, ["disk", "largest-files", str(tmp_path), "-n", "5"])
    assert result.exit_code == 0


def test_disk_largest_dirs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.bin").write_bytes(b"x" * 5000)
    result = runner.invoke(app, ["disk", "largest-dirs", str(tmp_path)])
    assert result.exit_code == 0


def test_disk_reclaim(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "lib.js").write_bytes(b"x" * 5000)
    monkeypatch.setenv("JANITOR_DISK__MIN_SIZE_MB", "0")
    result = runner.invoke(app, ["disk", "reclaim", str(tmp_path)])
    assert result.exit_code == 0
    assert "node_modules" in result.stdout


def test_disk_reclaim_clean(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "src").mkdir()
    monkeypatch.setenv("JANITOR_DISK__MIN_SIZE_MB", "0")
    result = runner.invoke(app, ["disk", "reclaim", str(tmp_path)])
    assert result.exit_code == 0
    assert "No common offenders" in result.stdout


# --- logs -----------------------------------------------------------------


def test_logs_size_and_clean(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import os
    import time

    log = tmp_path / "old.log"
    log.write_bytes(b"x" * (11 * 1024 * 1024))
    old = time.time() - 60 * 86400
    os.utime(log, (old, old))
    monkeypatch.setenv("JANITOR_LOGS__PATHS", f'["{tmp_path}"]')

    size_result = runner.invoke(app, ["logs", "size"])
    assert size_result.exit_code == 0
    assert "Large Log Files" in size_result.stdout
    assert "11.0 MB" in size_result.stdout

    clean_result = runner.invoke(app, ["--yes", "logs", "clean", "--max-age", "30"])
    assert clean_result.exit_code == 0
    assert not log.exists()


def test_logs_clean_dry_run(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import os
    import time

    log = tmp_path / "old.log"
    log.write_bytes(b"x" * 1000)
    old = time.time() - 60 * 86400
    os.utime(log, (old, old))
    monkeypatch.setenv("JANITOR_LOGS__PATHS", f'["{tmp_path}"]')
    result = runner.invoke(app, ["--dry-run", "logs", "clean", "--max-age", "30"])
    assert result.exit_code == 0
    assert log.exists()


# --- supabase -------------------------------------------------------------


def _make_supabase_project(base, name):  # type: ignore[no-untyped-def]
    root = base / name
    (root / "supabase").mkdir(parents=True)
    (root / "supabase" / "config.toml").write_text("project_id='x'\n", encoding="utf-8")
    return root


def test_supabase_list(
    patched_runner: FakeRunner, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.supabase.which", lambda _: "/usr/bin/supabase")
    _make_supabase_project(tmp_path, "alpha")
    patched_runner.stub(["supabase", "status"], stdout="API URL: http://x")
    monkeypatch.setenv("JANITOR_SUPABASE__SEARCH_PATHS", f'["{tmp_path}"]')
    result = runner.invoke(app, ["supabase", "list"])
    assert result.exit_code == 0
    assert "alpha" in result.stdout


def test_supabase_backup(
    patched_runner: FakeRunner, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.supabase.which", lambda _: "/usr/bin/supabase")
    _make_supabase_project(tmp_path, "alpha")
    monkeypatch.setenv("JANITOR_SUPABASE__SEARCH_PATHS", f'["{tmp_path}"]')
    monkeypatch.setenv("JANITOR_SUPABASE__BACKUP_DIR", str(tmp_path / "bk"))
    result = runner.invoke(app, ["--yes", "supabase", "backup", "alpha"])
    assert result.exit_code == 0
    assert "backup" in result.stdout.lower()


def test_supabase_backup_no_cli(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("janitor.services.supabase.which", lambda _: None)
    result = runner.invoke(app, ["supabase", "backup"])
    assert result.exit_code == 1


# --- k3s ------------------------------------------------------------------


def test_k3s_status_with_cluster(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: "/usr/bin/kubectl")
    patched_runner.stub(["kubectl", "config", "current-context"], stdout="ctx")
    patched_runner.stub(["kubectl", "cluster-info"], stdout="ok")
    patched_runner.stub(
        ["kubectl", "get", "nodes"],
        stdout=json.dumps(
            {
                "items": [
                    {
                        "metadata": {"name": "n1", "labels": {}},
                        "status": {
                            "conditions": [{"type": "Ready", "status": "True"}],
                            "nodeInfo": {"kubeletVersion": "v1.30"},
                        },
                    }
                ]
            }
        ),
    )
    patched_runner.stub(
        ["kubectl", "get", "pods"],
        stdout=json.dumps(
            {
                "items": [
                    {
                        "metadata": {"namespace": "d", "name": "bad"},
                        "status": {"phase": "Pending", "containerStatuses": []},
                    }
                ]
            }
        ),
    )
    result = runner.invoke(app, ["k3s", "status"])
    assert result.exit_code == 0
    assert "Unhealthy" in result.stdout


def test_k3s_cleanup(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: "/usr/bin/kubectl")
    patched_runner.stub(["kubectl", "cluster-info"], stdout="ok")
    patched_runner.stub(
        ["kubectl", "get", "jobs"],
        stdout=json.dumps(
            {
                "items": [
                    {
                        "metadata": {"namespace": "d", "name": "done"},
                        "status": {"succeeded": 1, "active": 0},
                    }
                ]
            }
        ),
    )
    result = runner.invoke(app, ["--yes", "k3s", "cleanup"])
    assert result.exit_code == 0
    assert "done" in result.stdout
