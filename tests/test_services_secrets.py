"""Tests for the secrets (Varlock + 1Password) service."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    assert "DEMO_APP_SERVICE_ROLE_KEY" in text  # prefix derived from app name
    assert "demo-app-prod/service_role_key" in text  # item-per-env convention


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


# ---- pull ------------------------------------------------------------------


def test_op_available(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.secrets.which", lambda _: "/usr/bin/op")
    assert SecretsService(runner=fake_runner).op_available() is True
    monkeypatch.setattr("janitor.services.secrets.which", lambda _: None)
    assert SecretsService(runner=fake_runner).op_available() is False


def test_read_op_ref_returns_value(fake_runner: FakeRunner) -> None:
    fake_runner.stub(["op", "read"], stdout="s3cr3t\n")
    assert SecretsService(runner=fake_runner).read_op_ref("op://v/i/f") == "s3cr3t"


def test_read_op_ref_raises_on_failure(fake_runner: FakeRunner) -> None:
    fake_runner.stub(["op", "read"], returncode=1, stderr="not signed in")
    with pytest.raises(RuntimeError, match="not signed in"):
        SecretsService(runner=fake_runner).read_op_ref("op://v/i/f")


def test_pull_writes_env_file_chmod_600(tmp_path: Path, fake_runner: FakeRunner) -> None:
    fake_runner.stub(["op", "read", "op://V/stk-prod/db_url"], stdout="postgres://u:p@h/db")
    fake_runner.stub(["op", "read", "op://V/stk-prod/service_role_key"], stdout="eyJkey")
    env_file = tmp_path / "stk.env"
    report = SecretsService(runner=fake_runner).pull(
        [
            ("STK_PROD_DATABASE_URL", "op://V/stk-prod/db_url"),
            ("STK_PROD_SERVICE_ROLE_KEY", "op://V/stk-prod/service_role_key"),
        ],
        env_file,
        local_key_env="STK_LOCAL_SERVICE_ROLE_KEY",
    )
    assert report.written == ["STK_PROD_DATABASE_URL", "STK_PROD_SERVICE_ROLE_KEY"]
    text = env_file.read_text(encoding="utf-8")
    assert "export STK_PROD_DATABASE_URL='postgres://u:p@h/db'" in text
    assert "export STK_PROD_SERVICE_ROLE_KEY='eyJkey'" in text
    # local key noted as manual, not resolved from op
    assert "STK_LOCAL_SERVICE_ROLE_KEY" in text
    assert "supabase status" in text
    assert (env_file.stat().st_mode & 0o777) == 0o600


def test_pull_quotes_values_with_single_quotes(tmp_path: Path, fake_runner: FakeRunner) -> None:
    fake_runner.stub(["op", "read"], stdout="pa'ss")
    env_file = tmp_path / "x.env"
    SecretsService(runner=fake_runner).pull([("SECRET", "op://v/i/f")], env_file)
    # embedded single quote is shell-escaped so `source` stays safe
    assert "export SECRET='pa'\"'\"'ss'" in env_file.read_text(encoding="utf-8")


def test_pull_dry_run_writes_nothing(tmp_path: Path, make_runner) -> None:  # type: ignore[no-untyped-def]
    env_file = tmp_path / "stk.env"
    report = SecretsService(runner=make_runner(dry_run=True)).pull(
        [("STK_PROD_DATABASE_URL", "op://V/stk-prod/db_url")], env_file
    )
    assert report.dry_run is True
    assert not env_file.exists()


def test_pull_preserves_existing_local_key(tmp_path: Path, fake_runner: FakeRunner) -> None:
    fake_runner.stub(["op", "read"], stdout="val")
    env_file = tmp_path / "stk.env"
    env_file.write_text("export STK_LOCAL_SERVICE_ROLE_KEY='localkey123'\n", encoding="utf-8")
    SecretsService(runner=fake_runner).pull(
        [("STK_PROD_DATABASE_URL", "op://V/i/db_url")],
        env_file,
        local_key_env="STK_LOCAL_SERVICE_ROLE_KEY",
    )
    text = env_file.read_text(encoding="utf-8")
    assert "export STK_LOCAL_SERVICE_ROLE_KEY='localkey123'" in text  # preserved, not clobbered


def test_pull_ignores_placeholder_local_key(tmp_path: Path, fake_runner: FakeRunner) -> None:
    fake_runner.stub(["op", "read"], stdout="val")
    env_file = tmp_path / "stk.env"
    env_file.write_text("export STK_LOCAL_SERVICE_ROLE_KEY='REPLACE_ME_local'\n", encoding="utf-8")
    SecretsService(runner=fake_runner).pull(
        [("STK_PROD_DATABASE_URL", "op://V/i/db_url")],
        env_file,
        local_key_env="STK_LOCAL_SERVICE_ROLE_KEY",
    )
    text = env_file.read_text(encoding="utf-8")
    assert "REPLACE_ME_local" not in text  # placeholder dropped
    assert "supabase status" in text  # replaced by the manual-setup note
