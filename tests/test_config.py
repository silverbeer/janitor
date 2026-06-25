"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from janitor.config import JanitorConfig, config_path, load_config


def test_defaults() -> None:
    config = JanitorConfig()
    assert config.dry_run is False
    assert config.assume_yes is False
    assert config.disk.top_n == 20
    assert config.logs.max_age_days == 30


def test_config_path_uses_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")
    assert config_path() == Path("/tmp/xdg/janitor/config.toml")


def test_config_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_path() == Path.home() / ".config" / "janitor" / "config.toml"


def test_load_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JANITOR_DRY_RUN", raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "dry_run = true\n[disk]\ntop_n = 99\nmin_size_mb = 5\n",
        encoding="utf-8",
    )
    config = load_config(cfg)
    assert config.dry_run is True
    assert config.disk.top_n == 99
    assert config.disk.min_size_mb == 5


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[disk]\ntop_n = 10\n", encoding="utf-8")
    monkeypatch.setenv("JANITOR_DISK__TOP_N", "42")
    config = load_config(cfg)
    assert config.disk.top_n == 42


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nope.toml")
    assert config.disk.top_n == 20


def test_supabase_retention_defaults() -> None:
    sb = JanitorConfig().supabase
    assert sb.resolved_retention("anything") == (5, 0, 2000)
    assert sb.resolved_backup_dir("anything") == sb.backup_dir.expanduser()


def test_supabase_per_project_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[supabase]\n"
        "retention_count = 5\n"
        "[supabase.projects.mt]\n"
        'backup_dir = "/tmp/mt-backups"\n'
        "retention_count = 3\n"
        "max_dir_size_mb = 500\n",
        encoding="utf-8",
    )
    sb = load_config(cfg).supabase
    # Per-project override wins; unset retention_days falls back to the default.
    assert sb.resolved_retention("mt") == (3, 0, 500)
    assert sb.resolved_backup_dir("mt") == Path("/tmp/mt-backups")
    # A project with no override block uses the shared defaults.
    assert sb.resolved_retention("other") == (5, 0, 2000)


def test_supabase_resolved_prod_db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[supabase.projects.stk]\nprod_db_url_env = 'STK_PROD_DB_URL'\n",
        encoding="utf-8",
    )
    sb = load_config(cfg).supabase
    monkeypatch.delenv("STK_PROD_DB_URL", raising=False)
    assert sb.resolved_prod_db_url("stk") is None  # env unset
    monkeypatch.setenv("STK_PROD_DB_URL", "postgresql://u:p@prod/db")
    assert sb.resolved_prod_db_url("stk") == "postgresql://u:p@prod/db"
    # Project without prod_db_url_env configured -> None.
    assert sb.resolved_prod_db_url("other") is None
