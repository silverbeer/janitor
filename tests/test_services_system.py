"""Tests for the system probe service."""

from __future__ import annotations

from janitor.models.common import HealthStatus
from janitor.services.system import SystemService
from tests.conftest import FakeRunner


def test_python_check() -> None:
    check = SystemService().python_check()
    assert check.available is True
    assert check.version


def test_uv_check_present(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.system.which", lambda _: "/usr/bin/uv")
    fake_runner.stub(["uv", "--version"], stdout="uv 0.11.0")
    check = SystemService(runner=fake_runner).uv_check()
    assert check.status is HealthStatus.OK
    assert check.version == "uv 0.11.0"


def test_uv_check_missing(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.system.which", lambda _: None)
    check = SystemService(runner=fake_runner).uv_check()
    assert check.available is False
    assert check.status is HealthStatus.WARN


def test_docker_check_daemon_down(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.system.which", lambda _: "/usr/bin/docker")
    fake_runner.stub(["docker", "info"], returncode=1, stderr="cannot connect")
    check = SystemService(runner=fake_runner).docker_check()
    assert check.available is True
    assert check.status is HealthStatus.WARN


def test_docker_check_ok(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.system.which", lambda _: "/usr/bin/docker")
    fake_runner.stub(["docker", "info"], stdout="29.0.0")
    check = SystemService(runner=fake_runner).docker_check()
    assert check.status is HealthStatus.OK


def test_kubernetes_check_no_context(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.system.which", lambda _: "/usr/bin/kubectl")
    fake_runner.stub(["kubectl", "config", "current-context"], returncode=1)
    check = SystemService(runner=fake_runner).kubernetes_check()
    assert check.status is HealthStatus.WARN


def test_all_checks(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.system.which", lambda _: None)
    checks = SystemService(runner=fake_runner).all_checks()
    assert len(checks) == 6
    assert checks[0].name == "Python"
