"""Kubernetes / k3s service via kubectl."""

from __future__ import annotations

import json
from typing import Any

from janitor.config import K3sConfig
from janitor.models.k3s import K3sImage, K3sNode, K3sPod, K3sStatus, repository_of
from janitor.services.shell import ShellRunner, which

__all__ = ["K3sQueryError", "K3sService", "normalize_image_ref"]

RANCHER_EXEC = ["rdctl", "shell", "--", "sudo"]
RANCHER_CONTEXT = "rancher-desktop"
#: Rancher Desktop's data disk; /var/lib (containerd) is a bind of it.
RANCHER_TRIM_PATHS = ["/mnt/data"]
#: Workload kinds whose pod templates name images. CronJobs matter most: between
#: runs they have no pod and no container, so only the spec says the image is needed.
WORKLOAD_KINDS = "deployments,statefulsets,daemonsets,replicasets,jobs,cronjobs,pods"


class K3sQueryError(RuntimeError):
    """A query that decides what is in use failed. Never prune on a partial view."""


def normalize_image_ref(ref: str) -> str:
    """Fully qualify an image reference the way containerd names it.

    ``trd:latest`` -> ``docker.io/library/trd:latest``; ``redis`` ->
    ``docker.io/library/redis:latest``; ``ghcr.io/x/y@sha256:..`` unchanged.
    Workload specs use the short form, the runtime the long one.
    """
    name, at, digest = ref.partition("@")
    tag = ""
    slash, colon = name.rfind("/"), name.rfind(":")
    if colon > slash:
        name, tag = name[:colon], name[colon + 1 :]
    first, _, rest = name.partition("/")
    if not rest or ("." not in first and ":" not in first and first != "localhost"):
        name = f"docker.io/{name}"
    domain, _, path = name.partition("/")
    if domain == "docker.io" and "/" not in path:
        name = f"docker.io/library/{path}"
    if at:
        return f"{name}@{digest}"
    return f"{name}:{tag or 'latest'}"


class K3sService:
    """Wrapper around ``kubectl`` for cluster health and cleanup."""

    def __init__(self, runner: ShellRunner | None = None, config: K3sConfig | None = None) -> None:
        self.runner = runner or ShellRunner()
        self.config = config or K3sConfig()

    # --- node runtime (images, trim) -------------------------------------------

    @property
    def is_rancher(self) -> bool:
        """True when the node is Rancher Desktop's VM (reached through ``rdctl``)."""
        return self.node_exec == RANCHER_EXEC

    @property
    def node_exec(self) -> list[str]:
        if self.config.node_exec is not None:
            return list(self.config.node_exec)
        return list(RANCHER_EXEC) if which("rdctl") else []

    @property
    def kube_context(self) -> str | None:
        if self.config.kube_context:
            return self.config.kube_context
        return RANCHER_CONTEXT if self.is_rancher else None

    def _crictl(self, *args: str) -> list[str]:
        return [*self.node_exec, "k3s", "crictl", *args]

    def _kubectl(self, *args: str) -> list[str]:
        context = self.kube_context
        return ["kubectl", *(["--context", context] if context else []), *args]

    def _query(self, command: list[str]) -> dict[str, Any]:
        result = self.runner.run(command, timeout=120)
        data = self._load(result.stdout) if result.ok else {}
        if not data:
            detail = result.stderr.strip()[:200] or "no output"
            raise K3sQueryError(f"{' '.join(command)} failed: {detail}")
        return data

    def images(self) -> list[K3sImage]:
        """Every runtime image, marked in use / stale.

        Raises:
            K3sQueryError: if the image list, the container list or the workload
                specs can't be read. An empty "in use" set would mark every image
                prunable, so a failed query is fatal, never an empty answer.
        """
        raw = self._query(self._crictl("images", "-o", "json")).get("images", [])
        containers = self._query(self._crictl("ps", "-a", "-o", "json")).get("containers", [])
        used_ids = {c.get("imageRef", "") for c in containers} | {
            (c.get("image") or {}).get("image", "") for c in containers
        }
        refs = self.workload_image_refs()

        images = [
            K3sImage(
                id=item.get("id", ""),
                tags=list(item.get("repoTags") or []),
                digests=list(item.get("repoDigests") or []),
                size=int(item.get("size") or 0),
                pinned=bool(item.get("pinned")),
            )
            for item in raw
        ]
        for image in images:
            names = {normalize_image_ref(n) for n in image.tags + image.digests}
            image.in_use = image.pinned or image.id in used_ids or bool(names & refs)
        self._mark_stale(images)
        return images

    def workload_image_refs(self) -> set[str]:
        """Normalized image refs named by any workload's pod template (incl. init)."""
        data = self._query(self._kubectl("get", WORKLOAD_KINDS, "--all-namespaces", "-o", "json"))
        refs: set[str] = set()
        for item in data.get("items", []):
            spec = item.get("spec", {})
            template = spec.get("jobTemplate", {}).get("spec", {}).get("template") or spec.get(
                "template"
            )
            pod_spec = (template or item).get("spec", {})
            for key in ("containers", "initContainers", "ephemeralContainers"):
                for container in pod_spec.get(key) or []:
                    if container.get("image"):
                        refs.add(normalize_image_ref(container["image"]))
        return refs

    @staticmethod
    def _mark_stale(images: list[K3sImage]) -> None:
        """Tagged, unused, and superseded by an in-use image of the same repository.

        Unlike Docker there is no "newest" fallback: a repository with nothing in
        use keeps all its tags (it may be a manual rollback copy). ``latest`` is
        never stale.
        """
        in_use_repos = {repo for image in images if image.in_use for repo in image.repositories}
        for image in images:
            image.stale = (
                not image.in_use
                and not image.dangling
                and not any(t.endswith(":latest") for t in image.tags)
                and bool({repository_of(t) for t in image.tags} & in_use_repos)
            )

    def remove_images(self, images: list[K3sImage]) -> list[K3sImage]:
        """``crictl rmi`` each image by id (all its names). Honors dry-run.

        The runtime refuses an image a container still uses, which is a second
        guard behind the in-use check.
        """
        removed: list[K3sImage] = []
        for image in images:
            result = self.runner.run(self._crictl("rmi", image.id), mutating=True, timeout=120)
            if result.ok:
                removed.append(image)
        return removed

    def trim(self) -> str:
        """``fstrim`` the node's filesystems so freed blocks go back to the host.

        Only meaningful for a VM (Rancher Desktop): deleting inside the VM does
        not shrink its disk file on the host until the blocks are discarded.
        Mount points are explicit: Rancher's Alpine VM ships busybox ``fstrim``,
        which has no ``-a``.
        """
        if not self.node_exec:
            raise K3sQueryError("trim needs a VM node (Rancher Desktop / node_exec configured)")
        paths = self.config.trim_paths or (RANCHER_TRIM_PATHS if self.is_rancher else ["/"])
        lines: list[str] = []
        for path in paths:
            cmd = [*self.node_exec, "fstrim", "-v", path]
            result = self.runner.run(cmd, mutating=True, timeout=600)
            if not result.ok:
                detail = (result.stderr or result.stdout).strip()[:200]
                raise K3sQueryError(f"fstrim {path} failed: {detail}")
            lines.append(result.stdout.strip())
        return "\n".join(line for line in lines if line)

    # --- cluster health --------------------------------------------------------

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
