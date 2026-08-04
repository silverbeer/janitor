"""Tests for the Docker service."""

from __future__ import annotations

import json

from janitor.services.docker import DockerService
from tests.conftest import FakeRunner


def _df_line(type_: str, total: int, active: int, size: str, reclaimable: str) -> str:
    return json.dumps(
        {
            "Type": type_,
            "TotalCount": total,
            "Active": active,
            "Size": size,
            "Reclaimable": reclaimable,
        }
    )


def test_usage_parsing(fake_runner: FakeRunner) -> None:
    stdout = "\n".join(
        [
            _df_line("Images", 11, 11, "12.5GB", "3.7GB (29%)"),
            _df_line("Containers", 11, 11, "25.6MB", "0B (0%)"),
            _df_line("Local Volumes", 9, 3, "225.8MB", "134.2MB (59%)"),
        ]
    )
    fake_runner.stub(["docker", "system", "df"], stdout=stdout)
    service = DockerService(runner=fake_runner)
    usage = service.usage()
    assert len(usage.records) == 3
    assert usage.records[0].size == int(12.5 * 1024**3)
    assert usage.total_reclaimable > 0


def test_is_available_true(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.docker.which", lambda _: "/usr/bin/docker")
    fake_runner.stub(["docker", "info"], stdout="ok")
    assert DockerService(runner=fake_runner).is_available() is True


def test_is_available_no_binary(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.docker.which", lambda _: None)
    assert DockerService(runner=fake_runner).is_available() is False


def test_images_flags_dangling(fake_runner: FakeRunner) -> None:
    stdout = "\n".join(
        [
            json.dumps({"ID": "a", "Repository": "nginx", "Tag": "latest", "Size": "100MB"}),
            json.dumps({"ID": "b", "Repository": "<none>", "Tag": "<none>", "Size": "50MB"}),
        ]
    )
    fake_runner.stub(["docker", "images"], stdout=stdout)
    images = DockerService(runner=fake_runner).images()
    assert len(images) == 2
    assert images[1].dangling is True
    assert images[0].dangling is False


def _img(id_: str, repo: str, tag: str, size: str, created: str) -> str:
    return json.dumps(
        {"ID": id_, "Repository": repo, "Tag": tag, "Size": size, "CreatedAt": created}
    )


def _stub_images(fake_runner: FakeRunner, lines: list[str], containers: str = "") -> None:
    fake_runner.stub(["docker", "images"], stdout="\n".join(lines))
    fake_runner.stub(["docker", "ps"], stdout=containers)


def test_stale_marks_superseded_versions(fake_runner: FakeRunner) -> None:
    _stub_images(
        fake_runner,
        [
            _img("sha256:a", "supabase/realtime", "v2.1", "1GB", "2026-01-01 10:00:00 -0500 EST"),
            _img("sha256:b", "supabase/realtime", "v2.2", "1GB", "2026-02-01 10:00:00 -0500 EST"),
            _img("sha256:c", "supabase/realtime", "v2.3", "1GB", "2026-03-01 10:00:00 -0500 EST"),
        ],
        containers="supabase/realtime:v2.2\n",
    )
    by_tag = {i.tag: i for i in DockerService(runner=fake_runner).images()}
    # The in-use tag wins over the newest one.
    assert by_tag["v2.2"].stale is False
    assert by_tag["v2.1"].stale is True
    assert by_tag["v2.3"].stale is True


def test_stale_keeps_newest_when_nothing_is_in_use(fake_runner: FakeRunner) -> None:
    _stub_images(
        fake_runner,
        [
            _img("sha256:a", "redis", "6", "100MB", "2026-01-01 10:00:00 -0500 EST"),
            _img("sha256:b", "redis", "7", "100MB", "2026-05-01 10:00:00 -0500 EST"),
        ],
    )
    by_tag = {i.tag: i for i in DockerService(runner=fake_runner).images()}
    assert by_tag["7"].stale is False
    assert by_tag["6"].stale is True


def test_single_version_repository_is_never_stale(fake_runner: FakeRunner) -> None:
    _stub_images(
        fake_runner,
        [_img("sha256:a", "nginx", "latest", "100MB", "2026-01-01 10:00:00 -0500 EST")],
    )
    assert DockerService(runner=fake_runner).images()[0].stale is False


def test_stale_respects_bare_repo_and_id_references(fake_runner: FakeRunner) -> None:
    _stub_images(
        fake_runner,
        [
            _img("sha256:a", "nginx", "latest", "100MB", "2026-01-01 10:00:00 -0500 EST"),
            _img("sha256:b", "nginx", "1.2", "100MB", "2026-06-01 10:00:00 -0500 EST"),
            _img("sha256:deadbeefcafe01", "app", "old", "1GB", "2026-01-01 10:00:00 -0500 EST"),
            _img("sha256:f00d", "app", "new", "1GB", "2026-06-01 10:00:00 -0500 EST"),
        ],
        # bare "nginx" means nginx:latest; the app container names an image by id.
        containers="nginx\ndeadbeefcafe01\n",
    )
    by_ref = {i.reference: i for i in DockerService(runner=fake_runner).images()}
    assert by_ref["nginx:latest"].stale is False
    assert by_ref["nginx:1.2"].stale is True
    assert by_ref["app:old"].stale is False
    assert by_ref["app:new"].stale is True


def test_latest_tag_is_never_stale(fake_runner: FakeRunner) -> None:
    _stub_images(
        fake_runner,
        [
            _img("sha256:a", "app", "latest", "1GB", "2026-01-01 10:00:00 -0500 EST"),
            _img("sha256:b", "app", "v2", "1GB", "2026-06-01 10:00:00 -0500 EST"),
            _img("sha256:c", "app", "v1", "1GB", "2025-06-01 10:00:00 -0500 EST"),
        ],
    )
    by_tag = {i.tag: i for i in DockerService(runner=fake_runner).images()}
    # `latest` is older than v2 but still kept; only the pinned old tag goes.
    assert by_tag["latest"].stale is False
    assert by_tag["v2"].stale is False
    assert by_tag["v1"].stale is True


def test_stale_images_helper_filters(fake_runner: FakeRunner) -> None:
    _stub_images(
        fake_runner,
        [
            _img("sha256:a", "redis", "6", "100MB", "2026-01-01 10:00:00 -0500 EST"),
            _img("sha256:b", "redis", "7", "100MB", "2026-05-01 10:00:00 -0500 EST"),
        ],
    )
    stale = DockerService(runner=fake_runner).stale_images()
    assert [i.tag for i in stale] == ["6"]


def test_dangling_images_are_not_marked_stale(fake_runner: FakeRunner) -> None:
    _stub_images(
        fake_runner,
        [
            _img("sha256:a", "<none>", "<none>", "50MB", "2026-01-01 10:00:00 -0500 EST"),
            _img("sha256:b", "<none>", "<none>", "50MB", "2026-05-01 10:00:00 -0500 EST"),
        ],
    )
    images = DockerService(runner=fake_runner).images()
    assert all(i.dangling for i in images)
    assert not any(i.stale for i in images)


def test_remove_images_reports_removed(fake_runner: FakeRunner) -> None:
    removed = DockerService(runner=fake_runner).remove_images(["redis:6", "redis:5"])
    assert removed == ["redis:6", "redis:5"]
    assert ["docker", "rmi", "redis:6"] in fake_runner.calls


def test_volumes_in_use(fake_runner: FakeRunner) -> None:
    fake_runner.stub(
        ["docker", "volume", "ls", "--filter"],
        stdout="dangling-vol\n",
    )
    fake_runner.stub(
        ["docker", "volume", "ls", "--format"],
        stdout="\n".join(
            [
                json.dumps({"Name": "active-vol", "Driver": "local"}),
                json.dumps({"Name": "dangling-vol", "Driver": "local"}),
            ]
        ),
    )
    volumes = DockerService(runner=fake_runner).volumes()
    by_name = {v.name: v for v in volumes}
    assert by_name["active-vol"].in_use is True
    assert by_name["dangling-vol"].in_use is False


def _usage_with_all_types(fake_runner: FakeRunner) -> object:
    stdout = "\n".join(
        [
            _df_line("Images", 10, 2, "100GB", "80GB (80%)"),
            _df_line("Containers", 10, 2, "100MB", "60MB (60%)"),
            _df_line("Local Volumes", 5, 2, "3GB", "120MB (4%)"),
            _df_line("Build Cache", 100, 0, "50GB", "17GB (34%)"),
        ]
    )
    fake_runner.stub(["docker", "system", "df"], stdout=stdout)
    return DockerService(runner=fake_runner).usage()


def test_usage_record_lookup_ignores_case_and_spaces(fake_runner: FakeRunner) -> None:
    usage = _usage_with_all_types(fake_runner)
    assert usage.record("local volumes") is not None  # type: ignore[attr-defined]
    assert usage.reclaimable_for("LocalVolumes") == 120 * 1024**2  # type: ignore[attr-defined]
    assert usage.reclaimable_for("Nonexistent") == 0  # type: ignore[attr-defined]


def test_reclaimable_estimate_safe_counts_only_dangling_images(
    fake_runner: FakeRunner,
) -> None:
    usage = _usage_with_all_types(fake_runner)
    fake_runner.stub(
        ["docker", "images"],
        stdout=json.dumps({"ID": "a", "Repository": "<none>", "Tag": "<none>", "Size": "2GB"}),
    )
    estimate = DockerService(runner=fake_runner).reclaimable_estimate(
        usage,  # type: ignore[arg-type]
        all_images=False,
        volumes=False,
        build_cache=True,
    )
    # dangling image + containers + build cache — NOT the 80GB of unused tagged images.
    assert estimate == 2 * 1024**3 + 60 * 1024**2 + 17 * 1024**3
    assert estimate < usage.total_reclaimable  # type: ignore[attr-defined]


def test_reclaimable_estimate_aggressive_reaches_total(fake_runner: FakeRunner) -> None:
    usage = _usage_with_all_types(fake_runner)
    estimate = DockerService(runner=fake_runner).reclaimable_estimate(
        usage,  # type: ignore[arg-type]
        all_images=True,
        volumes=True,
        build_cache=True,
    )
    assert estimate == usage.total_reclaimable  # type: ignore[attr-defined]


def test_reclaimable_estimate_excludes_disabled_build_cache(fake_runner: FakeRunner) -> None:
    usage = _usage_with_all_types(fake_runner)
    fake_runner.stub(["docker", "images"], stdout="")
    estimate = DockerService(runner=fake_runner).reclaimable_estimate(
        usage,  # type: ignore[arg-type]
        all_images=False,
        volumes=False,
        build_cache=False,
    )
    assert estimate == 60 * 1024**2


def test_prune_safe(fake_runner: FakeRunner) -> None:
    ran = DockerService(runner=fake_runner).prune(all_images=False, build_cache=True)
    assert ["docker", "system", "prune", "--force"] in fake_runner.calls
    assert any("builder" in cmd for cmd in ran)


def test_prune_aggressive(fake_runner: FakeRunner) -> None:
    DockerService(runner=fake_runner).prune(all_images=True, volumes=True)
    sys_call = next(c for c in fake_runner.calls if "system" in c)
    assert "--all" in sys_call
    assert "--volumes" in sys_call


def test_prune_dry_run(make_runner) -> None:  # type: ignore[no-untyped-def]
    runner = make_runner(dry_run=True)
    DockerService(runner=runner).prune()
    # Commands still recorded but mutating ones are no-ops.
    assert runner.calls
