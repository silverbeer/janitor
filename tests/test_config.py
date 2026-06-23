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
