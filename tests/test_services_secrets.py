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


# ---- init ------------------------------------------------------------------


def test_scaffold_schema_creates_with_import(tmp_path: Path, fake_runner: FakeRunner) -> None:
    path, status = SecretsService(runner=fake_runner).scaffold_schema(tmp_path, "demo-app")
    assert status == "created"
    text = path.read_text(encoding="utf-8")
    assert "@import(" in text
    assert "DEMO_APP_PROD_SERVICE_ROLE_KEY" in text  # prefix derived from app name


def test_scaffold_schema_keeps_existing(tmp_path: Path, fake_runner: FakeRunner) -> None:
    (tmp_path / ".env.schema").write_text("mine\n", encoding="utf-8")
    path, status = SecretsService(runner=fake_runner).scaffold_schema(tmp_path, "demo")
    assert status == "exists"
    assert path.read_text(encoding="utf-8") == "mine\n"  # untouched


def test_scaffold_schema_dry_run(tmp_path: Path, make_runner) -> None:  # type: ignore[no-untyped-def]
    path, status = SecretsService(runner=make_runner(dry_run=True)).scaffold_schema(
        tmp_path, "demo"
    )
    assert status == "would-create"
    assert not path.exists()


def test_ensure_gitignore_adds_negation_when_ignored(
    tmp_path: Path, fake_runner: FakeRunner
) -> None:
    # Stub `git check-ignore` to report the file IS ignored (exit 0).
    fake_runner.stub(["git"], returncode=0)
    (tmp_path / ".gitignore").write_text(".env.*\n", encoding="utf-8")
    status = SecretsService(runner=fake_runner).ensure_gitignore_allows(tmp_path)
    assert status == "fixed"
    assert "!.env.schema" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_ensure_gitignore_noop_when_not_ignored(tmp_path: Path, fake_runner: FakeRunner) -> None:
    fake_runner.stub(["git"], returncode=1)  # not ignored
    assert SecretsService(runner=fake_runner).ensure_gitignore_allows(tmp_path) == "ok"


# ---- doctor ----------------------------------------------------------------


def test_parse_schema_vars(tmp_path: Path, fake_runner: FakeRunner) -> None:
    schema = tmp_path / ".env.schema"
    schema.write_text(
        "# @import(base)\n# @required @sensitive\nSTK_PROD_KEY=op(op://v/i/f)\nLOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    assert SecretsService(runner=fake_runner).parse_schema_vars(schema) == {
        "STK_PROD_KEY",
        "LOG_LEVEL",
    }


def test_helm_secret_env_vars(tmp_path: Path, fake_runner: FakeRunner) -> None:
    helm = tmp_path / "helm"
    helm.mkdir()
    (helm / "deploy.yaml").write_text(
        "        env:\n"
        "        - name: SUPABASE_KEY\n"
        "          valueFrom:\n"
        "            secretKeyRef:\n"
        "              name: app\n"
        "              key: supabase-key\n"
        "        - name: PLAIN_CONFIG\n"
        "          value: hello\n",
        encoding="utf-8",
    )
    names = SecretsService(runner=fake_runner).helm_secret_env_vars(helm)
    assert names == {"SUPABASE_KEY"}  # PLAIN_CONFIG has no secretKeyRef


def test_parity_flags_only_cloud(fake_runner: FakeRunner) -> None:
    report = SecretsService(runner=fake_runner).parity(
        schema_vars={"A", "LOG_LEVEL"}, cloud_vars={"A", "SECRET_B"}
    )
    assert report.matched == ["A"]
    assert report.only_schema == ["LOG_LEVEL"]
    assert report.only_cloud == ["SECRET_B"]
    assert report.healthy is False  # SECRET_B in cloud but not schema
