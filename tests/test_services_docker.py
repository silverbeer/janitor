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
