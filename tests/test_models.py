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
