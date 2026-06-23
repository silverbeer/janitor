"""Kubernetes / k3s service via kubectl."""

from __future__ import annotations

import json
from typing import Any

from janitor.models.k3s import K3sNode, K3sPod, K3sStatus
from janitor.services.shell import ShellRunner, which

__all__ = ["K3sService"]


class K3sService:
    """Wrapper around ``kubectl`` for cluster health and cleanup."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self.runner = runner or ShellRunner()

    def is_available(self) -> bool:
        """True when kubectl exists and a cluster is reachable."""
        if which("kubectl") is None:
            return False
        return self.runner.run(["kubectl", "cluster-info"], timeout=15).ok

    def status(self) -> K3sStatus:
        """Return aggregate cluster status (nodes + pods)."""
        if which("kubectl") is None:
            return K3sStatus(available=False)
        context = self.runner.capture(["kubectl", "config", "current-context"]) or None
        nodes = self._nodes()
        pods = self._pods()
        available = bool(nodes) or self.is_available()
        return K3sStatus(available=available, context=context, nodes=nodes, pods=pods)

    def _nodes(self) -> list[K3sNode]:
        result = self.runner.run(["kubectl", "get", "nodes", "-o", "json"])
        if not result.ok:
            return []
        data = self._load(result.stdout)
        nodes: list[K3sNode] = []
        for item in data.get("items", []):
            metadata = item.get("metadata", {})
            status = item.get("status", {})
            conditions = {c["type"]: c["status"] for c in status.get("conditions", [])}
            labels = metadata.get("labels", {})
            roles = ",".join(
                k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/")
            )
            nodes.append(
                K3sNode(
                    name=metadata.get("name", "?"),
                    ready=conditions.get("Ready") == "True",
                    roles=roles or "worker",
                    version=status.get("nodeInfo", {}).get("kubeletVersion", ""),
                )
            )
        return nodes

    def _pods(self) -> list[K3sPod]:
        result = self.runner.run(["kubectl", "get", "pods", "--all-namespaces", "-o", "json"])
        if not result.ok:
            return []
        data = self._load(result.stdout)
        pods: list[K3sPod] = []
        for item in data.get("items", []):
            metadata = item.get("metadata", {})
            status = item.get("status", {})
            container_statuses = status.get("containerStatuses", [])
            ready = bool(container_statuses) and all(
                c.get("ready", False) for c in container_statuses
            )
            restarts = sum(c.get("restartCount", 0) for c in container_statuses)
            pods.append(
                K3sPod(
                    namespace=metadata.get("namespace", "default"),
                    name=metadata.get("name", "?"),
                    phase=status.get("phase", "Unknown"),
                    ready=ready,
                    restarts=restarts,
                )
            )
        return pods

    def cleanup_completed_jobs(self) -> list[str]:
        """Delete succeeded jobs across all namespaces. Honors dry-run.

        Returns:
            Names (``namespace/job``) that were deleted or would be deleted.
        """
        result = self.runner.run(
            [
                "kubectl",
                "get",
                "jobs",
                "--all-namespaces",
                "-o",
                "json",
            ]
        )
        if not result.ok:
            return []
        data = self._load(result.stdout)
        targets: list[tuple[str, str]] = []
        for item in data.get("items", []):
            metadata = item.get("metadata", {})
            status = item.get("status", {})
            if status.get("succeeded", 0) and not status.get("active", 0):
                targets.append((metadata.get("namespace", "default"), metadata.get("name", "?")))
        deleted: list[str] = []
        for namespace, name in targets:
            self.runner.run(
                ["kubectl", "delete", "job", name, "-n", namespace],
                mutating=True,
            )
            deleted.append(f"{namespace}/{name}")
        return deleted

    @staticmethod
    def _load(text: str) -> dict[str, Any]:
        try:
            return json.loads(text)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            return {}
