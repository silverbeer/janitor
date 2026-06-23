"""Kubernetes / k3s models."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["K3sNode", "K3sPod", "K3sStatus"]


class K3sNode(BaseModel):
    """A cluster node."""

    name: str
    ready: bool
    roles: str = ""
    version: str = ""


class K3sPod(BaseModel):
    """A pod and its health summary."""

    namespace: str
    name: str
    phase: str
    ready: bool = False
    restarts: int = 0

    @property
    def healthy(self) -> bool:
        """True when the pod is Running/Succeeded and ready."""
        return self.phase in {"Running", "Succeeded"} and (self.ready or self.phase == "Succeeded")


class K3sStatus(BaseModel):
    """Aggregate cluster status."""

    available: bool
    context: str | None = None
    nodes: list[K3sNode] = Field(default_factory=list)
    pods: list[K3sPod] = Field(default_factory=list)

    @property
    def failed_pods(self) -> list[K3sPod]:
        """Pods that are neither healthy nor completed."""
        return [p for p in self.pods if not p.healthy]
