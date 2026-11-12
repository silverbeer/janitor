"""k3s runtime images: what is in use, what prune removes, and trim (SB-1515)."""

from __future__ import annotations

import json

import pytest

from janitor.config import K3sConfig
from janitor.models.k3s import K3sImage, repository_of
from janitor.services.k3s import (
    RANCHER_EXEC,
    K3sQueryError,
    K3sService,
    normalize_image_ref,
)
from tests.conftest import FakeRunner

CRICTL = [*RANCHER_EXEC, "k3s", "crictl"]
KUBECTL = ["kubectl", "--context", "rancher-desktop"]


def _image(id_: str, tags: list[str] | None = None, digests: list[str] | None = None, **kw):  # type: ignore[no-untyped-def]
    return {
        "id": f"sha256:{id_}",
        "repoTags": tags or [],
        "repoDigests": digests or [],
        "size": "1000",
        **kw,
    }


IMAGES = [
    # CronJob-only image: no pod, no container between runs.
    _image("cron", ["docker.io/library/trd:latest"]),
    # Old pull of the cronjob's repo, tag moved on.
    _image("oldpull", digests=["ghcr.io/x/app@sha256:aaa"]),
    # Deployed worker + a superseded build + a rollback-style copy of an unused repo.
    _image("worker", ["docker.io/library/worker:v2"]),
    _image("workerold", ["docker.io/library/worker:v1"]),
    _image("workerlatest", ["docker.io/library/worker:latest"]),
    _image("other", ["docker.io/library/tool:pre-1"]),
    # Used only by an exited container.
    _image("exited", digests=["ghcr.io/y/job@sha256:bbb"]),
    # Pinned sandbox image (no workload names it).
    _image("pause", ["docker.io/rancher/mirrored-pause:3.6"], pinned=True),
    # Referenced by digest in a spec.
    _image("bydigest", digests=["ghcr.io/z/svc@sha256:ccc"]),
]

CONTAINERS = [
    {"imageRef": "sha256:exited", "image": {"image": "sha256:exited"}, "state": "CONTAINER_EXITED"},
]

WORKLOADS = {
    "items": [
        {
            "kind": "CronJob",
            "spec": {
                "jobTemplate": {
                    "spec": {"template": {"spec": {"containers": [{"image": "trd:latest"}]}}}
                }
            },
        },
        {
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {
                        "initContainers": [{"image": "ghcr.io/z/svc@sha256:ccc"}],
                        "containers": [{"image": "worker:v2"}],
                    }
                }
            },
        },
        {"kind": "Pod", "spec": {"containers": [{"image": "redis"}]}},
    ]
}


@pytest.fixture
def rancher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.k3s.which", lambda name: f"/bin/{name}")


def _stub(
    runner: FakeRunner,
    *,
    fail: str | None = None,
    workloads_stdout: str | None = None,
) -> None:
    """Stub crictl + kubectl; ``fail`` makes one query ("images", "ps", "get") error."""

    def kw(name: str, stdout: str) -> dict[str, object]:
        if name == fail:
            return {"returncode": 1, "stderr": "connection refused"}
        return {"stdout": stdout}

    runner.stub([*CRICTL, "images"], **kw("images", json.dumps({"images": IMAGES})))  # type: ignore[arg-type]
    runner.stub([*CRICTL, "ps"], **kw("ps", json.dumps({"containers": CONTAINERS})))  # type: ignore[arg-type]
    workloads = json.dumps(WORKLOADS) if workloads_stdout is None else workloads_stdout
    runner.stub([*KUBECTL, "get"], **kw("get", workloads))  # type: ignore[arg-type]


def _by_id(images: list[K3sImage]) -> dict[str, K3sImage]:
    return {i.id.removeprefix("sha256:"): i for i in images}


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("trd:latest", "docker.io/library/trd:latest"),
        ("redis", "docker.io/library/redis:latest"),
        ("bitnami/kubectl:1.30", "docker.io/bitnami/kubectl:1.30"),
        ("ghcr.io/x/app:main", "ghcr.io/x/app:main"),
        ("ghcr.io/x/app@sha256:aaa", "ghcr.io/x/app@sha256:aaa"),
        ("localhost:5000/app", "localhost:5000/app:latest"),
        ("localhost/app:1", "localhost/app:1"),
        ("docker.io/library/trd:latest", "docker.io/library/trd:latest"),
    ],
)
def test_normalize_image_ref(ref: str, expected: str) -> None:
    assert normalize_image_ref(ref) == expected


def test_repository_of() -> None:
    assert repository_of("localhost:5000/app:1") == "localhost:5000/app"
    assert repository_of("ghcr.io/x/app@sha256:aaa") == "ghcr.io/x/app"


def test_in_use_covers_cronjobs_digests_exited_and_pinned(
    fake_runner: FakeRunner, rancher: None
) -> None:
    _stub(fake_runner)
    images = _by_id(K3sService(runner=fake_runner).images())
    for id_ in ("cron", "worker", "exited", "pause", "bydigest"):
        assert images[id_].in_use, id_
        assert not images[id_].prunable(stale=True), id_


def test_safe_prune_takes_only_unused_dangling(fake_runner: FakeRunner, rancher: None) -> None:
    _stub(fake_runner)
    images = K3sService(runner=fake_runner).images()
    assert {i.id for i in images if i.prunable(stale=False)} == {"sha256:oldpull"}


def test_stale_takes_superseded_tags_but_not_latest_or_unused_repos(
    fake_runner: FakeRunner, rancher: None
) -> None:
    _stub(fake_runner)
    images = _by_id(K3sService(runner=fake_runner).images())
    assert images["workerold"].stale
    assert not images["workerlatest"].stale  # latest is a pointer, kept
    assert not images["other"].stale  # nothing of that repo in use: maybe a rollback copy
    assert {k for k, i in images.items() if i.prunable(stale=True)} == {"oldpull", "workerold"}


def test_workload_query_uses_rancher_context_not_current(
    fake_runner: FakeRunner, rancher: None
) -> None:
    _stub(fake_runner)
    K3sService(runner=fake_runner).images()
    kubectl_calls = [c for c in fake_runner.calls if c[0] == "kubectl"]
    assert kubectl_calls and all(c[1:3] == ["--context", "rancher-desktop"] for c in kubectl_calls)
    assert "cronjobs" in kubectl_calls[0][4]


@pytest.mark.parametrize("failing", ["images", "ps", "get"])
def test_any_failed_query_aborts(fake_runner: FakeRunner, rancher: None, failing: str) -> None:
    _stub(fake_runner, fail=failing)
    with pytest.raises(K3sQueryError, match="connection refused"):
        K3sService(runner=fake_runner).images()


def test_empty_workload_output_aborts(fake_runner: FakeRunner, rancher: None) -> None:
    _stub(fake_runner, workloads_stdout="")
    with pytest.raises(K3sQueryError):
        K3sService(runner=fake_runner).images()


def test_remove_images_by_id_and_dry_run(rancher: None) -> None:
    runner = FakeRunner(dry_run=True)
    target = K3sImage(id="sha256:oldpull")
    removed = K3sService(runner=runner).remove_images([target])
    assert removed == [target]
    assert runner.calls == [[*CRICTL, "rmi", "sha256:oldpull"]]


def test_remove_reports_only_successes(fake_runner: FakeRunner, rancher: None) -> None:
    fake_runner.stub([*CRICTL, "rmi", "sha256:busy"], returncode=1)
    service = K3sService(runner=fake_runner)
    removed = service.remove_images([K3sImage(id="sha256:busy"), K3sImage(id="sha256:ok")])
    assert [i.id for i in removed] == ["sha256:ok"]


def test_trim_runs_fstrim_in_vm(fake_runner: FakeRunner, rancher: None) -> None:
    fake_runner.stub([*RANCHER_EXEC, "fstrim"], stdout="/mnt/data: 10 GiB trimmed\n")
    assert K3sService(runner=fake_runner).trim() == "/mnt/data: 10 GiB trimmed"
    # busybox fstrim has no -a: the mount point is explicit.
    assert fake_runner.calls == [[*RANCHER_EXEC, "fstrim", "-v", "/mnt/data"]]


def test_trim_configured_paths(fake_runner: FakeRunner, rancher: None) -> None:
    fake_runner.stub([*RANCHER_EXEC, "fstrim"], stdout="ok")
    config = K3sConfig(trim_paths=["/a", "/b"])
    assert K3sService(runner=fake_runner, config=config).trim() == "ok\nok"
    assert [c[-1] for c in fake_runner.calls] == ["/a", "/b"]


def test_trim_failure_raises(fake_runner: FakeRunner, rancher: None) -> None:
    fake_runner.stub([*RANCHER_EXEC, "fstrim"], returncode=1, stderr="not permitted")
    with pytest.raises(K3sQueryError, match="not permitted"):
        K3sService(runner=fake_runner).trim()


def test_trim_needs_a_vm(fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: None)
    with pytest.raises(K3sQueryError, match="VM"):
        K3sService(runner=fake_runner).trim()


def test_config_overrides(fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: None)
    service = K3sService(runner=fake_runner)
    assert service.node_exec == [] and service.kube_context is None and not service.is_rancher
    service = K3sService(
        runner=fake_runner, config=K3sConfig(node_exec=["sudo"], kube_context="homelab")
    )
    assert service.node_exec == ["sudo"]
    assert service.kube_context == "homelab"


def test_image_reference_forms() -> None:
    assert K3sImage(id="sha256:" + "a" * 64, tags=["x:1"]).reference == "x:1"
    assert (
        K3sImage(id="sha256:1", digests=["r/a@sha256:" + "b" * 64]).reference == "r/a@bbbbbbbbbbbb"
    )
    assert K3sImage(id="sha256:" + "c" * 64).reference == "c" * 12


# --- BuildKit build cache (SB-1631) ------------------------------------------

BUILDCTL = [*RANCHER_EXEC, "buildctl", "--addr", "unix:///run/buildkit/buildkitd.sock"]
DU = json.dumps(
    [
        {"id": "a", "size": 1000, "inUse": False},
        {"id": "b", "size": 500, "inUse": True},
        {"id": "c", "size": 250, "inUse": False},
    ]
)


def test_build_cache_sums_reclaimable_excluding_in_use(
    fake_runner: FakeRunner, rancher: None
) -> None:
    fake_runner.stub([*BUILDCTL, "du"], stdout=DU)
    cache = K3sService(runner=fake_runner).build_cache()
    assert cache is not None
    assert (cache.records, cache.total, cache.reclaimable) == (3, 1750, 1250)


def test_build_cache_empty_is_null(fake_runner: FakeRunner, rancher: None) -> None:
    fake_runner.stub([*BUILDCTL, "du"], stdout="null\n")
    cache = K3sService(runner=fake_runner).build_cache()
    assert cache is not None and cache.reclaimable == 0


@pytest.mark.parametrize(
    "stub",
    [{"returncode": 1, "stderr": "no such socket"}, {"stdout": "not json"}, {"stdout": '{"a": 1}'}],
)
def test_build_cache_unreadable_is_none(
    fake_runner: FakeRunner, rancher: None, stub: dict[str, object]
) -> None:
    fake_runner.stub([*BUILDCTL, "du"], **stub)  # type: ignore[arg-type]
    assert K3sService(runner=fake_runner).build_cache() is None


def test_build_cache_skipped_without_buildkit(
    fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rdctl and no buildkit_addr: never shell out, never raise."""
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: None)
    service = K3sService(runner=fake_runner)
    assert service.buildkit_addr is None
    assert service.build_cache() is None
    assert fake_runner.calls == []
    with pytest.raises(K3sQueryError, match="no buildkitd"):
        service.prune_build_cache()


def test_build_cache_addr_from_config(fake_runner: FakeRunner) -> None:
    config = K3sConfig(node_exec=["ssh", "node"], buildkit_addr="tcp://127.0.0.1:1234")
    fake_runner.stub(["ssh", "node", "buildctl"], stdout="[]")
    K3sService(runner=fake_runner, config=config).build_cache()
    assert fake_runner.calls[-1][:5] == [
        "ssh",
        "node",
        "buildctl",
        "--addr",
        "tcp://127.0.0.1:1234",
    ]


def test_prune_build_cache_sums_freed_lines(fake_runner: FakeRunner, rancher: None) -> None:
    fake_runner.stub(
        [*BUILDCTL, "prune"],
        stdout='{"id": "a", "size": 1000}\nnoise\n{"id": "c", "size": 250}\n',
    )
    assert K3sService(runner=fake_runner).prune_build_cache() == 1250
    assert fake_runner.calls[-1] == [*BUILDCTL, "prune", "--all", "--format", "json"]


def test_prune_build_cache_dry_run_frees_nothing(rancher: None) -> None:
    fake = FakeRunner(dry_run=True)
    assert K3sService(runner=fake).prune_build_cache() == 0


def test_prune_build_cache_failure_raises(fake_runner: FakeRunner, rancher: None) -> None:
    fake_runner.stub([*BUILDCTL, "prune"], returncode=1, stderr="boom")
    with pytest.raises(K3sQueryError, match="boom"):
        K3sService(runner=fake_runner).prune_build_cache()


# --- maintain + state (SB-1632) ------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from janitor.models.k3s import K3sMaintainResult  # noqa: E402
from janitor.services.k3s import (  # noqa: E402
    last_maintain_age_days,
    load_maintain_result,
    save_maintain_result,
)
from janitor.services.schedule import build_plist  # noqa: E402

FSTRIM = [*RANCHER_EXEC, "fstrim"]


def test_maintain_runs_every_step(fake_runner: FakeRunner, rancher: None) -> None:
    _stub(fake_runner)
    fake_runner.stub([*BUILDCTL, "du"], stdout=DU)
    fake_runner.stub([*BUILDCTL, "prune"], stdout='{"size": 1250}\n')
    fake_runner.stub(FSTRIM, stdout="/mnt/data: 2 GiB trimmed")
    result = K3sService(runner=fake_runner).maintain()
    assert result.failures == []
    assert result.images_removed == 1  # "oldpull": the only dangling, unused image
    assert result.build_cache_freed == 1250
    assert "2 GiB" in result.trim
    assert not any("rmi" in c and "sha256:workerold" in c for c in fake_runner.calls), (
        "maintain must never take --stale images"
    )


def test_maintain_continues_past_a_failed_image_query(
    fake_runner: FakeRunner, rancher: None
) -> None:
    """Cluster down must not stop the build-cache prune or the trim."""
    _stub(fake_runner, fail="get")
    fake_runner.stub([*BUILDCTL, "du"], stdout=DU)
    fake_runner.stub([*BUILDCTL, "prune"], stdout='{"size": 1250}\n')
    result = K3sService(runner=fake_runner).maintain()
    assert result.images_removed == 0
    assert result.build_cache_freed == 1250
    assert [f.split(":")[0] for f in result.failures] == ["images"]
    assert any("fstrim" in c for c in fake_runner.calls)
    assert not any("rmi" in c for c in fake_runner.calls)


def test_maintain_records_buildkit_and_trim_failures(
    fake_runner: FakeRunner, rancher: None
) -> None:
    _stub(fake_runner)
    fake_runner.stub([*BUILDCTL, "du"], returncode=1)
    fake_runner.stub(FSTRIM, returncode=1, stderr="busy")
    result = K3sService(runner=fake_runner).maintain()
    assert [f.split(":")[0] for f in result.failures] == ["build cache", "trim"]


def test_maintain_records_build_cache_prune_failure(fake_runner: FakeRunner, rancher: None) -> None:
    _stub(fake_runner)
    fake_runner.stub([*BUILDCTL, "du"], stdout=DU)
    fake_runner.stub([*BUILDCTL, "prune"], returncode=1, stderr="boom")
    result = K3sService(runner=fake_runner).maintain()
    assert any(f.startswith("build cache: buildctl prune failed") for f in result.failures)


def test_maintain_skips_empty_cache_and_vm_steps_without_vm(
    fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare node (no rdctl, no buildkit_addr): images only, no buildctl/fstrim."""
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: None)
    fake_runner.stub(["k3s", "crictl", "images"], stdout=json.dumps({"images": []}))
    fake_runner.stub(["k3s", "crictl", "ps"], stdout=json.dumps({"containers": []}))
    fake_runner.stub(["kubectl", "get"], stdout=json.dumps({"items": []}))
    result = K3sService(runner=fake_runner).maintain()
    assert result.failures == []
    assert not any("buildctl" in c or "fstrim" in c for c in fake_runner.calls)


def test_maintain_result_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "k3s.json"
    assert load_maintain_result(path) is None
    assert last_maintain_age_days(path) is None
    save_maintain_result(K3sMaintainResult(ran_at="x", dry_run=True), path)
    assert not path.exists(), "dry runs are never saved"
    ran = datetime.now(UTC) - timedelta(days=2)
    save_maintain_result(K3sMaintainResult(ran_at=ran.isoformat(), build_cache_freed=7), path)
    loaded = load_maintain_result(path)
    assert loaded is not None and loaded.build_cache_freed == 7
    age = last_maintain_age_days(path)
    assert age is not None and 1.9 < age < 2.1
    save_maintain_result(K3sMaintainResult(ran_at="not a date"), path)
    assert last_maintain_age_days(path) is None


def test_build_plist_weekly() -> None:
    import plistlib

    weekly = plistlib.loads(
        build_plist(label="x", program=["jt"], hour=4, minute=0, log_file=Path("/l"), weekday=0)
    )
    assert weekly["StartCalendarInterval"] == {"Hour": 4, "Minute": 0, "Weekday": 0}
    daily = plistlib.loads(
        build_plist(label="x", program=["jt"], hour=4, minute=0, log_file=Path("/l"))
    )
    assert "Weekday" not in daily["StartCalendarInterval"]
