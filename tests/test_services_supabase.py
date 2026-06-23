"""Tests for the Supabase service."""

from __future__ import annotations

from pathlib import Path

from janitor.services.supabase import SupabaseService
from tests.conftest import FakeRunner


def _make_project(base: Path, name: str) -> Path:
    root = base / name
    (root / "supabase").mkdir(parents=True)
    (root / "supabase" / "config.toml").write_text("project_id = 'x'\n", encoding="utf-8")
    return root


def test_discover(tmp_path: Path, fake_runner: FakeRunner) -> None:
    _make_project(tmp_path, "alpha")
    _make_project(tmp_path, "beta")
    projects = SupabaseService(runner=fake_runner).discover([tmp_path])
    names = {p.name for p in projects}
    assert names == {"alpha", "beta"}


def test_discover_skips_missing(fake_runner: FakeRunner) -> None:
    assert SupabaseService(runner=fake_runner).discover([Path("/nope-xyz")]) == []


def test_status_running(tmp_path: Path, fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.supabase.which", lambda _: "/usr/bin/supabase")
    root = _make_project(tmp_path, "alpha")
    fake_runner.stub(["supabase", "status"], stdout="API URL: http://localhost")
    project = SupabaseService(runner=fake_runner).discover([tmp_path])[0]
    updated = SupabaseService(runner=fake_runner).status(project)
    assert updated.running is True
    assert root.name == "alpha"


def test_backup(tmp_path: Path, fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.supabase.which", lambda _: "/usr/bin/supabase")
    _make_project(tmp_path, "alpha")
    service = SupabaseService(runner=fake_runner)
    project = service.discover([tmp_path])[0]
    backup_dir = tmp_path / "backups"
    destination = service.backup(project, backup_dir)
    assert destination.parent == backup_dir
    assert destination.name.startswith("alpha-")
    assert destination.suffix == ".sql"
    assert any("db" in c and "dump" in c for c in fake_runner.calls)


def test_cli_unavailable(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.supabase.which", lambda _: None)
    assert SupabaseService(runner=fake_runner).cli_available() is False
