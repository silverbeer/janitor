"""Tests for the Supabase service."""

from __future__ import annotations

import os
from datetime import datetime
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


# ---- backup-dir hygiene ----------------------------------------------------


def _write_backup(backup_dir: Path, name: str, *, age_days: int = 0, size: int = 16) -> Path:
    """Create a fake dump file with a backdated mtime."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / name
    path.write_bytes(b"x" * size)
    if age_days:
        old = datetime.now().timestamp() - age_days * 86_400
        os.utime(path, (old, old))
    return path


def test_list_backups_newest_first(tmp_path: Path, fake_runner: FakeRunner) -> None:
    backup_dir = tmp_path / "backups"
    _write_backup(backup_dir, "alpha-20260101-000000.sql", age_days=10)
    _write_backup(backup_dir, "alpha-20260601-000000.sql", age_days=1)
    _write_backup(backup_dir, "beta-20260601-000000.sql")  # other project, ignored
    files = SupabaseService(runner=fake_runner).list_backups("alpha", backup_dir)
    assert [f.path.name for f in files] == [
        "alpha-20260601-000000.sql",
        "alpha-20260101-000000.sql",
    ]


def test_list_backups_missing_dir(tmp_path: Path, fake_runner: FakeRunner) -> None:
    assert SupabaseService(runner=fake_runner).list_backups("alpha", tmp_path / "nope") == []


def test_report_flags_over_size_and_count(tmp_path: Path, fake_runner: FakeRunner) -> None:
    backup_dir = tmp_path / "backups"
    for i in range(4):
        _write_backup(backup_dir, f"alpha-2026010{i}-000000.sql", age_days=i, size=1024 * 1024)
    report = SupabaseService(runner=fake_runner).report(
        "alpha", backup_dir, retention_count=2, retention_days=0, max_dir_size_mb=1
    )
    assert report.count == 4
    assert report.over_count is True
    assert report.over_size is True  # 4 MB > 1 MB ceiling
    assert len(report.prunable) == 2  # two oldest beyond count=2
    assert report.healthy is False


def test_report_healthy_within_limits(tmp_path: Path, fake_runner: FakeRunner) -> None:
    backup_dir = tmp_path / "backups"
    _write_backup(backup_dir, "alpha-20260601-000000.sql")
    report = SupabaseService(runner=fake_runner).report(
        "alpha", backup_dir, retention_count=5, retention_days=0, max_dir_size_mb=0
    )
    assert report.healthy is True
    assert report.over_size is False  # max_size 0 == unlimited


def test_prune_removes_by_count(tmp_path: Path, fake_runner: FakeRunner) -> None:
    backup_dir = tmp_path / "backups"
    for i in range(3):
        _write_backup(backup_dir, f"alpha-2026010{i}-000000.sql", age_days=i)
    pruned = SupabaseService(runner=fake_runner).prune_backups(
        "alpha", backup_dir, retention_count=1, retention_days=0
    )
    assert len(pruned) == 2
    remaining = list(backup_dir.glob("alpha-*.sql"))
    assert len(remaining) == 1


def test_prune_removes_by_age(tmp_path: Path, fake_runner: FakeRunner) -> None:
    backup_dir = tmp_path / "backups"
    _write_backup(backup_dir, "alpha-20260101-000000.sql", age_days=40)
    _write_backup(backup_dir, "alpha-20260601-000000.sql", age_days=1)
    pruned = SupabaseService(runner=fake_runner).prune_backups(
        "alpha", backup_dir, retention_count=0, retention_days=30
    )
    assert [p.path.name for p in pruned] == ["alpha-20260101-000000.sql"]


def test_prune_dry_run_keeps_files(tmp_path: Path, make_runner) -> None:  # type: ignore[no-untyped-def]
    backup_dir = tmp_path / "backups"
    for i in range(3):
        _write_backup(backup_dir, f"alpha-2026010{i}-000000.sql", age_days=i)
    runner = make_runner(dry_run=True)
    pruned = SupabaseService(runner=runner).prune_backups(
        "alpha", backup_dir, retention_count=1, retention_days=0
    )
    assert len(pruned) == 2  # reported as would-prune
    assert len(list(backup_dir.glob("alpha-*.sql"))) == 3  # nothing deleted
