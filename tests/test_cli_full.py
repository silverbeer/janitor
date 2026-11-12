"""Broader CLI coverage for command rendering and cleanup paths."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from janitor.config import JanitorConfig, SupabaseProjectConfig
from janitor.main import app
from janitor.models.system import AdminUser
from tests.conftest import FakeRunner
from tests.test_services_supabase import FakeAdmin

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


def _stale_image_stubs(patched_runner: FakeRunner) -> None:
    patched_runner.stub(
        ["docker", "images"],
        stdout="\n".join(
            [
                json.dumps(
                    {
                        "ID": "a",
                        "Repository": "redis",
                        "Tag": "6",
                        "Size": "100MB",
                        "CreatedAt": "2026-01-01 10:00:00 -0500 EST",
                    }
                ),
                json.dumps(
                    {
                        "ID": "b",
                        "Repository": "redis",
                        "Tag": "7",
                        "Size": "100MB",
                        "CreatedAt": "2026-05-01 10:00:00 -0500 EST",
                    }
                ),
            ]
        ),
    )
    patched_runner.stub(["docker", "ps"], stdout="")


def test_docker_images_stale_filter(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _docker_available(patched_runner, monkeypatch)
    _stale_image_stubs(patched_runner)
    result = runner.invoke(app, ["docker", "images", "--stale"])
    assert result.exit_code == 0
    assert "1 stale image(s)" in result.stdout


def test_docker_prune_stale(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker_available(patched_runner, monkeypatch)
    _stale_image_stubs(patched_runner)
    result = runner.invoke(app, ["--yes", "docker", "prune", "--stale"])
    assert result.exit_code == 0
    assert ["docker", "rmi", "redis:6"] in patched_runner.calls
    # The newest version must survive.
    assert ["docker", "rmi", "redis:7"] not in patched_runner.calls


def test_docker_prune_stale_conflicts_with_aggressive(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _docker_available(patched_runner, monkeypatch)
    result = runner.invoke(app, ["--yes", "docker", "prune", "--stale", "--aggressive"])
    assert result.exit_code == 2


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


# --- supabase sync-users --------------------------------------------------


def test_sync_users_missing_config(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("janitor.main.load_config", JanitorConfig)  # no projects configured
    result = runner.invoke(app, ["supabase", "sync-users", "stk"])
    assert result.exit_code == 1


def test_sync_users_happy_path(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = JanitorConfig()
    cfg.supabase.projects["stk"] = SupabaseProjectConfig(
        prod_api_url="https://ref.supabase.co",
        prod_service_key_env="PK",
        local_service_key_env="LK",
        user_passwords={"a@x.com": "letmein"},
    )
    monkeypatch.setattr("janitor.main.load_config", lambda: cfg)
    monkeypatch.setenv("PK", "prodkey")
    monkeypatch.setenv("LK", "localkey")

    prod = FakeAdmin([AdminUser(id="1", email="a@x.com"), AdminUser(id="2", email="b@x.com")])
    local = FakeAdmin()
    monkeypatch.setattr(
        "janitor.commands.supabase.make_admin_client",
        lambda url, key: prod if "supabase.co" in url else local,
    )

    result = runner.invoke(app, ["--yes", "supabase", "sync-users", "stk"])
    assert result.exit_code == 0
    assert "a@x.com" in result.stdout
    assert "synced" in result.stdout
    # Only the user_passwords target was synced, with its prod id preserved.
    assert [u.email for u, _ in local.upserts] == ["a@x.com"]
    assert local.upserts[0][0].id == "1"


# --- secrets (varlock) ----------------------------------------------------


def test_secrets_run_no_command(patched_runner: FakeRunner) -> None:
    result = runner.invoke(app, ["secrets", "run"])
    assert result.exit_code == 2


def test_secrets_run_varlock_missing(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("janitor.services.secrets.which", lambda _: None)
    result = runner.invoke(app, ["secrets", "run", "--", "echo", "hi"])
    assert result.exit_code == 1


def test_secrets_run_passthrough_exit_code(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("janitor.services.secrets.which", lambda _: "/usr/bin/varlock")
    patched_runner.exec_code = 7
    result = runner.invoke(app, ["secrets", "run", "--", "echo", "hi"])
    assert result.exit_code == 7
    assert ["varlock", "run", "--", "echo", "hi"] in patched_runner.calls


def test_secrets_base_writes_schema(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "janitor.services.secrets.config_path", lambda: tmp_path / "janitor" / "config.toml"
    )
    result = runner.invoke(app, ["secrets", "base"])
    assert result.exit_code == 0
    assert (tmp_path / "janitor" / "base" / ".env.schema").exists()


def test_secrets_init_scaffolds(
    patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "janitor.services.secrets.config_path", lambda: tmp_path / "cfg" / "config.toml"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    patched_runner.stub(["git"], returncode=1)  # .env.schema not ignored
    result = runner.invoke(app, ["secrets", "init", "myapp", "--path", str(repo)])
    assert result.exit_code == 0
    assert (repo / ".env.schema").exists()


def test_secrets_doctor_no_schema(patched_runner: FakeRunner, tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = runner.invoke(app, ["secrets", "doctor", "--path", str(tmp_path)])
    assert result.exit_code == 1


def test_secrets_doctor_reports_parity(patched_runner: FakeRunner, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".env.schema").write_text("STK_KEY=op(op://v/i/f)\n", encoding="utf-8")
    helm = tmp_path / "helm"
    helm.mkdir()
    (helm / "d.yaml").write_text(
        "        - name: SUPABASE_KEY\n          valueFrom:\n            secretKeyRef:\n"
        "              name: app\n              key: k\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["secrets", "doctor", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "SUPABASE_KEY" in result.stdout
    assert "in cloud but not in schema" in result.stdout


# --- k3s images / prune / trim (SB-1515) -----------------------------------

_K3S_CRICTL = ["rdctl", "shell", "--", "sudo", "k3s", "crictl"]


def _stub_k3s_images(fake: FakeRunner, *, empty: bool = False, kubectl_rc: int = 0) -> None:
    fake.stub(
        [*_K3S_CRICTL, "images"],
        stdout=json.dumps(
            {
                "images": []
                if empty
                else [
                    {"id": "sha256:used", "repoTags": ["docker.io/library/app:v2"], "size": "10"},
                    {"id": "sha256:old", "repoTags": ["docker.io/library/app:v1"], "size": "20"},
                    {"id": "sha256:gone", "repoDigests": ["ghcr.io/x/y@sha256:d"], "size": "30"},
                ]
            }
        ),
    )
    fake.stub([*_K3S_CRICTL, "ps"], stdout=json.dumps({"containers": []}))
    fake.stub(
        ["kubectl", "--context", "rancher-desktop", "get"],
        returncode=kubectl_rc,
        stdout=json.dumps(
            {"items": [{"spec": {"template": {"spec": {"containers": [{"image": "app:v2"}]}}}}]}
        ),
    )


@pytest.fixture
def rancher_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.k3s.which", lambda name: f"/bin/{name}")


def test_k3s_images(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner)
    result = runner.invoke(app, ["k3s", "images"])
    assert result.exit_code == 0
    assert "1 dangling" in result.stdout and "adds 1" in result.stdout
    result = runner.invoke(app, ["k3s", "images", "--stale"])
    assert "app:v2" not in result.stdout and "app:v1" in result.stdout


def test_k3s_prune_dry_run_mutates_nothing(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner)
    patched_runner.dry_run = True
    result = runner.invoke(app, ["--dry-run", "k3s", "prune", "--stale"])
    assert result.exit_code == 0
    assert "Would remove 2 of 2" in result.stdout


def test_k3s_prune_removes_and_suggests_trim(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner)
    result = runner.invoke(app, ["--yes", "k3s", "prune"])
    assert result.exit_code == 0
    assert [*_K3S_CRICTL, "rmi", "sha256:gone"] in patched_runner.calls
    assert [*_K3S_CRICTL, "rmi", "sha256:old"] not in patched_runner.calls
    assert "jt k3s trim" in result.stdout


def test_k3s_prune_nothing(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner, empty=True)
    result = runner.invoke(app, ["k3s", "prune"])
    assert "Nothing to prune" in result.stdout


def test_k3s_prune_aborts_on_query_failure(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner, kubectl_rc=1)
    result = runner.invoke(app, ["--yes", "k3s", "prune"])
    assert result.exit_code == 1
    assert not any("rmi" in c for c in patched_runner.calls)


def test_k3s_prune_declined(
    patched_runner: FakeRunner, rancher_cli: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_k3s_images(patched_runner)
    monkeypatch.setattr("janitor.commands.k3s.confirm", lambda *_a, **_k: False)
    result = runner.invoke(app, ["k3s", "prune"])
    assert "Aborted" in result.stdout
    assert not any("rmi" in c for c in patched_runner.calls)


_BUILDCTL = ["rdctl", "shell", "--", "sudo", "buildctl"]
_DU = json.dumps([{"size": 3 * 1024**3, "inUse": False}, {"size": 100, "inUse": True}])


def test_k3s_images_reports_build_cache(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner)
    patched_runner.stub(_BUILDCTL, stdout=_DU)
    result = runner.invoke(app, ["k3s", "images"], env={"COLUMNS": "200"})
    assert "BuildKit build cache" in result.stdout and "2 record(s)" in result.stdout


def test_k3s_prune_drops_build_cache(patched_runner: FakeRunner, rancher_cli: None) -> None:
    """SB-1631: 54 GB of build cache sat beside 0.3 GB of dangling images, unseen."""
    _stub_k3s_images(patched_runner, empty=True)
    patched_runner.stub([*_BUILDCTL, "--addr"], stdout=_DU)
    patched_runner.stub(
        [*_BUILDCTL, "--addr", "unix:///run/buildkit/buildkitd.sock", "prune"],
        stdout=json.dumps({"size": 3 * 1024**3}),
    )
    result = runner.invoke(app, ["--yes", "k3s", "prune"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    assert any("prune" in c and "buildctl" in c for c in patched_runner.calls)
    assert "Removed 3.0 GB of build cache" in result.stdout
    assert "jt k3s trim" in result.stdout


def test_k3s_prune_build_cache_dry_run(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner, empty=True)
    patched_runner.stub(_BUILDCTL, stdout=_DU)
    patched_runner.dry_run = True
    result = runner.invoke(app, ["--dry-run", "k3s", "prune"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "Would remove 3.0 GB of build cache" in result.stdout
    assert "jt k3s trim" not in result.stdout


def test_k3s_prune_no_build_cache_flag(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner, empty=True)
    patched_runner.stub(_BUILDCTL, stdout=_DU)
    result = runner.invoke(app, ["--yes", "k3s", "prune", "--no-build-cache"])
    assert "Nothing to prune" in result.stdout
    assert not any("buildctl" in c for c in patched_runner.calls)


def test_k3s_prune_build_cache_failure(patched_runner: FakeRunner, rancher_cli: None) -> None:
    _stub_k3s_images(patched_runner, empty=True)
    patched_runner.stub([*_BUILDCTL, "--addr"], stdout=_DU)
    patched_runner.stub(
        [*_BUILDCTL, "--addr", "unix:///run/buildkit/buildkitd.sock", "prune"],
        returncode=1,
        stderr="buildkitd gone",
    )
    result = runner.invoke(app, ["--yes", "k3s", "prune"])
    assert result.exit_code == 1


def test_k3s_trim(patched_runner: FakeRunner, rancher_cli: None) -> None:
    patched_runner.stub(["rdctl", "shell", "--", "sudo", "fstrim"], stdout="/mnt/data: 1 GiB")
    result = runner.invoke(app, ["k3s", "trim"])
    assert result.exit_code == 0 and "1 GiB" in result.stdout
    result = runner.invoke(app, ["--dry-run", "k3s", "trim"])
    assert "Dry-run" in result.stdout


def test_k3s_trim_without_vm(patched_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: None)
    result = runner.invoke(app, ["k3s", "trim"])
    assert result.exit_code == 1
