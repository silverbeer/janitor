"""Tests for the Supabase service."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from janitor.models.system import AdminUser
from janitor.services.supabase import SupabaseService, _is_local_url, _split_pg_secret
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


def test_resolve_projects_config_key_overrides_dirname(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    from janitor.config import SupabaseConfig, SupabaseProjectConfig

    # Discovered by directory name "myrunstreak.run"...
    repo = _make_project(tmp_path, "myrunstreak.run")
    # ...but configured under the key "stk" with an explicit path.
    config = SupabaseConfig(
        search_paths=[tmp_path],
        projects={"stk": SupabaseProjectConfig(path=repo)},
    )
    service = SupabaseService(runner=fake_runner)
    # The config key resolves to the configured path.
    stk = service.resolve_projects(config, "stk")
    assert len(stk) == 1
    assert stk[0].name == "stk"
    assert stk[0].path == repo
    # Both the discovered dir name and the config key are present when listing.
    names = {p.name for p in service.resolve_projects(config)}
    assert {"stk", "myrunstreak.run"} <= names
    # Unknown name resolves to nothing.
    assert service.resolve_projects(config, "nope") == []


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


# ---- restore-from-prod -----------------------------------------------------

_PROD_URL = "postgresql://produser:prodpass@prod.example.com:6543/postgres"
_LOCAL_URL = "postgresql://postgres:localpw@127.0.0.1:54322/postgres"


def test_split_pg_secret_strips_password() -> None:
    sanitized, password = _split_pg_secret(_PROD_URL)
    assert password == "prodpass"
    assert "prodpass" not in sanitized
    assert sanitized == "postgresql://produser@prod.example.com:6543/postgres"


def test_is_local_url() -> None:
    assert _is_local_url(_LOCAL_URL) is True
    assert _is_local_url(_PROD_URL) is False


def test_restore_rejects_nonlocal_target(tmp_path: Path, fake_runner: FakeRunner) -> None:
    with pytest.raises(ValueError, match="non-local target"):
        SupabaseService(runner=fake_runner).restore_from_prod(
            "alpha",
            tmp_path,
            local_db_url=_PROD_URL,  # not loopback -> guard trips
            prod_db_url=_PROD_URL,
            data_schemas=["public"],
        )


def test_restore_dry_run_runs_nothing(tmp_path: Path, make_runner) -> None:  # type: ignore[no-untyped-def]
    runner = make_runner(dry_run=True)
    result = SupabaseService(runner=runner).restore_from_prod(
        "alpha", tmp_path, local_db_url=_LOCAL_URL, prod_db_url=_PROD_URL, data_schemas=["public"]
    )
    assert result.dry_run is True
    assert result.loaded is False
    assert runner.calls == []  # nothing executed


def test_restore_runs_reset_dump_load(tmp_path: Path, fake_runner: FakeRunner) -> None:
    result = SupabaseService(runner=fake_runner).restore_from_prod(
        "alpha",
        tmp_path,
        local_db_url=_LOCAL_URL,
        prod_db_url=_PROD_URL,
        data_schemas=["public", "extra"],
    )
    assert result.reset is True and result.loaded is True
    programs = [c[0] for c in fake_runner.calls]
    assert programs == ["supabase", "pg_dump", "psql"]
    # Both schemas requested on the dump.
    dump_cmd = next(c for c in fake_runner.calls if c[0] == "pg_dump")
    assert dump_cmd.count("--schema") == 2
    # Passwords travel via env, never the argument vector.
    flat_argv = " ".join(arg for cmd in fake_runner.calls for arg in cmd)
    assert "prodpass" not in flat_argv
    assert "localpw" not in flat_argv
    assert {"PGPASSWORD": "prodpass"} in fake_runner.envs
    assert {"PGPASSWORD": "localpw"} in fake_runner.envs


# ---- sync-users ------------------------------------------------------------


class FakeAdmin:
    """In-memory AdminClient for tests."""

    def __init__(self, users: list[AdminUser] | None = None) -> None:
        self._users = users or []
        self.upserts: list[tuple[AdminUser, str]] = []
        self.fail_on: str | None = None

    def list_users(self) -> list[AdminUser]:
        return list(self._users)

    def upsert_user(self, user: AdminUser, password: str) -> None:
        if self.fail_on and user.email == self.fail_on:
            raise RuntimeError("boom")
        self.upserts.append((user, password))


def _pw(_user: AdminUser) -> str:
    return "pw"


def test_sync_users_default_targets(fake_runner: FakeRunner) -> None:
    prod = FakeAdmin([AdminUser(id="1", email="a@x.com"), AdminUser(id="2", email="b@x.com")])
    local = FakeAdmin()
    results = SupabaseService(runner=fake_runner).sync_users(
        prod_admin=prod, local_admin=local, password_for=_pw, targets=["a@x.com"]
    )
    assert [r.email for r in results] == ["a@x.com"]
    assert results[0].action == "synced"
    # Prod id preserved on the local upsert; password came from the resolver.
    assert local.upserts[0][0].id == "1"
    assert local.upserts[0][1] == "pw"


def test_sync_users_include_all_honors_excludes(fake_runner: FakeRunner) -> None:
    prod = FakeAdmin(
        [
            AdminUser(id="1", email="real@x.com"),
            AdminUser(id="2", email="contract_test_9@x.com"),
        ]
    )
    local = FakeAdmin()
    results = SupabaseService(runner=fake_runner).sync_users(
        prod_admin=prod,
        local_admin=local,
        password_for=_pw,
        include_all=True,
        exclude_patterns=["contract_test_%"],
    )
    assert [r.email for r in results] == ["real@x.com"]


def test_sync_users_dry_run_no_upsert(make_runner) -> None:  # type: ignore[no-untyped-def]
    prod = FakeAdmin([AdminUser(id="1", email="a@x.com")])
    local = FakeAdmin()
    results = SupabaseService(runner=make_runner(dry_run=True)).sync_users(
        prod_admin=prod, local_admin=local, password_for=_pw, targets=["a@x.com"]
    )
    assert results[0].dry_run is True
    assert local.upserts == []  # nothing written


def test_sync_users_failure_recorded(fake_runner: FakeRunner) -> None:
    prod = FakeAdmin([AdminUser(id="1", email="a@x.com")])
    local = FakeAdmin()
    local.fail_on = "a@x.com"
    results = SupabaseService(runner=fake_runner).sync_users(
        prod_admin=prod, local_admin=local, password_for=_pw, targets=["a@x.com"]
    )
    assert results[0].action == "failed"
    assert results[0].detail == "boom"
