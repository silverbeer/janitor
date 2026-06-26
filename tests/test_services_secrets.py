"""Tests for the secrets (Varlock + 1Password) service."""

from __future__ import annotations

from pathlib import Path

from janitor.services.secrets import ONE_PASSWORD_PLUGIN_VERSION, SecretsService
from tests.conftest import FakeRunner


def test_varlock_available(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.secrets.which", lambda _: "/usr/bin/varlock")
    assert SecretsService(runner=fake_runner).varlock_available() is True
    monkeypatch.setattr("janitor.services.secrets.which", lambda _: None)
    assert SecretsService(runner=fake_runner).varlock_available() is False


def test_write_base_schema(tmp_path: Path, fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "janitor.services.secrets.config_path", lambda: tmp_path / "janitor" / "config.toml"
    )
    path = SecretsService(runner=fake_runner).write_base_schema()
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert f"@plugin(@varlock/1password-plugin@{ONE_PASSWORD_PLUGIN_VERSION})" in text
    assert "@currentEnv=$APP_ENV" in text


def test_write_base_schema_dry_run(tmp_path: Path, make_runner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "janitor.services.secrets.config_path", lambda: tmp_path / "janitor" / "config.toml"
    )
    path = SecretsService(runner=make_runner(dry_run=True)).write_base_schema()
    assert not path.exists()  # dry-run writes nothing


def test_run_wraps_varlock(fake_runner: FakeRunner) -> None:
    code = SecretsService(runner=fake_runner).run(["jt", "supabase", "sync-users", "stk"])
    assert code == 0
    assert fake_runner.calls[-1] == ["varlock", "run", "--", "jt", "supabase", "sync-users", "stk"]


def test_run_returns_child_exit_code(fake_runner: FakeRunner) -> None:
    fake_runner.exec_code = 7
    assert SecretsService(runner=fake_runner).run(["false"]) == 7
