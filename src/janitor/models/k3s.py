"""Kubernetes / k3s models."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["K3sImage", "K3sNode", "K3sPod", "K3sStatus"]


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


class K3sImage(BaseModel):
    """One image in the cluster runtime, as the CRI (``crictl``) groups it.

    containerd keeps several names per image (``repo:tag``, ``repo@sha256:…`` and a
    bare ``sha256:<id>``); ``nerdctl images`` lists each name as a row, so a bare-id
    row looks "dangling" while its content is still tagged. The CRI view has one
    entry per image with all its names, which is the unit that can be removed.
    """

    id: str
    tags: list[str] = Field(default_factory=list)
    digests: list[str] = Field(default_factory=list)
    size: int = 0
    """Bytes as the CRI reports them (compressed content). The disk freed is larger:
    the unpacked snapshot is removed too."""
    pinned: bool = False
    in_use: bool = False
    """Named by a workload spec, used by a container (any state), or pinned."""
    stale: bool = False
    """Tagged and unused while another image of the same repository is in use."""

    @property
    def dangling(self) -> bool:
        """No tag points at this image any more (an old pull or rebuild)."""
        return not self.tags

    @property
    def short_id(self) -> str:
        return self.id.removeprefix("sha256:")[:12]

    @property
    def reference(self) -> str:
        """The most readable name: first tag, else first digest, else the id."""
        if self.tags:
            return self.tags[0]
        if self.digests:
            repo, _, digest = self.digests[0].partition("@")
            return f"{repo}@{digest.removeprefix('sha256:')[:12]}"
        return self.short_id

    @property
    def repositories(self) -> set[str]:
        return {repository_of(ref) for ref in self.tags + self.digests}

    def prunable(self, *, stale: bool) -> bool:
        """Safe tier: unused and dangling. ``stale`` adds superseded tagged images."""
        if self.in_use:
            return False
        return self.dangling or (stale and self.stale)


def repository_of(ref: str) -> str:
    """``docker.io/library/trd:latest`` -> ``docker.io/library/trd`` (digests too)."""
    name = ref.split("@", 1)[0]
    slash = name.rfind("/")
    colon = name.rfind(":")
    return name[:colon] if colon > slash else name
