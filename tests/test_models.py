"""Tests for Pydantic models and their derived properties."""

from __future__ import annotations

from pathlib import Path

from janitor.models.common import CommandResult, HealthStatus
from janitor.models.disk import DiskUsage
from janitor.models.docker import DockerUsage, DockerUsageRecord
from janitor.models.k3s import K3sPod, K3sStatus


def test_health_status_style_and_icon() -> None:
    assert HealthStatus.OK.style == "ok"
    assert HealthStatus.ERROR.icon == "✗"
    assert HealthStatus.WARN.style == "warn"


def test_command_result_ok() -> None:
    assert CommandResult(command=["x"], returncode=0).ok is True
    assert CommandResult(command=["x"], returncode=1).ok is False


def test_disk_usage_percent() -> None:
    usage = DiskUsage(path=Path("/"), total=100, used=25, free=75)
    assert usage.percent_used == 25.0


def test_disk_usage_zero_total() -> None:
    assert DiskUsage(path=Path("/"), total=0, used=0, free=0).percent_used == 0.0


def _usage(percent: int) -> DiskUsage:
    return DiskUsage(path=Path("/"), total=100, used=percent, free=100 - percent)


def test_disk_health_classifies_pressure() -> None:
    assert _usage(50).health() is HealthStatus.OK
    assert _usage(80).health() is HealthStatus.WARN
    assert _usage(98).health() is HealthStatus.ERROR


def test_disk_health_boundaries_are_inclusive() -> None:
    # 75 and 90 are the thresholds themselves, not the first value past them.
    assert _usage(74).health() is HealthStatus.OK
    assert _usage(75).health() is HealthStatus.WARN
    assert _usage(89).health() is HealthStatus.WARN
    assert _usage(90).health() is HealthStatus.ERROR


def test_disk_health_thresholds_are_overridable() -> None:
    # A machine with different headroom tunes these via DiskConfig.
    assert _usage(80).health(warn_percent=85, error_percent=95) is HealthStatus.OK
    assert _usage(80).health(warn_percent=50, error_percent=70) is HealthStatus.ERROR


def test_disk_health_of_an_empty_filesystem_is_ok() -> None:
    assert DiskUsage(path=Path("/"), total=0, used=0, free=0).health() is HealthStatus.OK


def test_docker_usage_totals() -> None:
    usage = DockerUsage(
        records=[
            DockerUsageRecord(type="Images", size=100, reclaimable=40),
            DockerUsageRecord(type="Volumes", size=50, reclaimable=10),
        ]
    )
    assert usage.total_size == 150
    assert usage.total_reclaimable == 50


def test_k3s_pod_health() -> None:
    assert K3sPod(namespace="d", name="a", phase="Running", ready=True).healthy is True
    assert K3sPod(namespace="d", name="b", phase="Succeeded").healthy is True
    assert K3sPod(namespace="d", name="c", phase="Failed").healthy is False


def test_k3s_failed_pods() -> None:
    status = K3sStatus(
        available=True,
        pods=[
            K3sPod(namespace="d", name="ok", phase="Running", ready=True),
            K3sPod(namespace="d", name="bad", phase="Pending"),
        ],
    )
    assert [p.name for p in status.failed_pods] == ["bad"]
