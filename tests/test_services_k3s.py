"""Tests for the k3s/kubernetes service."""

from __future__ import annotations

import json

from janitor.services.k3s import K3sService
from tests.conftest import FakeRunner

_NODES = {
    "items": [
        {
            "metadata": {"name": "node-1", "labels": {"node-role.kubernetes.io/master": ""}},
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "nodeInfo": {"kubeletVersion": "v1.30.0"},
            },
        }
    ]
}

_PODS = {
    "items": [
        {
            "metadata": {"namespace": "default", "name": "good"},
            "status": {
                "phase": "Running",
                "containerStatuses": [{"ready": True, "restartCount": 0}],
            },
        },
        {
            "metadata": {"namespace": "default", "name": "bad"},
            "status": {
                "phase": "CrashLoopBackOff",
                "containerStatuses": [{"ready": False, "restartCount": 7}],
            },
        },
    ]
}


def test_status(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: "/usr/bin/kubectl")
    fake_runner.stub(["kubectl", "config", "current-context"], stdout="my-ctx\n")
    fake_runner.stub(["kubectl", "get", "nodes"], stdout=json.dumps(_NODES))
    fake_runner.stub(["kubectl", "get", "pods"], stdout=json.dumps(_PODS))
    fake_runner.stub(["kubectl", "cluster-info"], stdout="running")
    status = K3sService(runner=fake_runner).status()
    assert status.available is True
    assert status.context == "my-ctx"
    assert status.nodes[0].ready is True
    assert status.nodes[0].roles == "master"
    assert len(status.failed_pods) == 1
    assert status.failed_pods[0].name == "bad"


def test_unavailable(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.k3s.which", lambda _: None)
    status = K3sService(runner=fake_runner).status()
    assert status.available is False


def test_cleanup_completed_jobs(fake_runner: FakeRunner) -> None:
    jobs = {
        "items": [
            {
                "metadata": {"namespace": "default", "name": "done"},
                "status": {"succeeded": 1, "active": 0},
            },
            {
                "metadata": {"namespace": "default", "name": "running"},
                "status": {"active": 1},
            },
        ]
    }
    fake_runner.stub(["kubectl", "get", "jobs"], stdout=json.dumps(jobs))
    deleted = K3sService(runner=fake_runner).cleanup_completed_jobs()
    assert deleted == ["default/done"]
    assert any("delete" in c and "done" in c for c in fake_runner.calls)
