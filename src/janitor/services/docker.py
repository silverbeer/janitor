"""Docker service: parse usage and perform cleanup."""

from __future__ import annotations

import json
from datetime import datetime

from janitor.models.docker import (
    DockerImage,
    DockerUsage,
    DockerUsageRecord,
    DockerVolume,
)
from janitor.services.shell import ShellRunner, which
from janitor.utils.format import parse_size

__all__ = ["DockerService"]


def _to_bytes(value: str) -> int:
    value = value.strip()
    if not value or value in {"0B", "N/A"}:
        return 0
    try:
        return parse_size(value)
    except ValueError:
        return 0


def _created_sort_key(created_at: str) -> datetime:
    """Sort key from ``docker images`` CreatedAt, oldest-first on unparseable input.

    Docker renders e.g. ``2026-06-29 10:11:12 -0400 EDT``. Only the local
    wall-clock prefix is used — all images on one host share a timezone, so the
    offset adds nothing to an ordering within a single repository.
    """
    try:
        return datetime.strptime(created_at[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.min


def _is_referenced(image: DockerImage, refs: set[str], repo: str) -> bool:
    """True when a container names this image, by tag or by ID.

    A bare ``repo`` reference means ``repo:latest``, and IDs may appear
    truncated, so both need explicit handling.
    """
    if image.reference in refs:
        return True
    if image.tag == "latest" and repo in refs:
        return True
    # --no-trunc yields "sha256:abc…" while a container names the bare short id.
    image_id = image.id.removeprefix("sha256:")
    return any(len(ref) >= 12 and image_id.startswith(ref) for ref in refs)


class DockerService:
    """Wrapper around the ``docker`` CLI."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self.runner = runner or ShellRunner()

    def is_available(self) -> bool:
        """True when the docker CLI is installed and the daemon responds."""
        if which("docker") is None:
            return False
        return self.runner.run(["docker", "info"]).ok

    def usage(self) -> DockerUsage:
        """Return parsed ``docker system df`` output."""
        result = self.runner.run(["docker", "system", "df", "--format", "{{json .}}"])
        records: list[DockerUsageRecord] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append(
                DockerUsageRecord(
                    type=raw.get("Type", "?"),
                    total=int(raw.get("TotalCount", 0) or 0),
                    active=int(raw.get("Active", 0) or 0),
                    size=_to_bytes(str(raw.get("Size", "0B"))),
                    reclaimable=_to_bytes(str(raw.get("Reclaimable", "0B")).split(" ")[0]),
                )
            )
        return DockerUsage(records=records)

    def images(self, *, dangling_only: bool = False) -> list[DockerImage]:
        """List Docker images, optionally only dangling ones."""
        cmd = ["docker", "images", "--format", "{{json .}}", "--no-trunc"]
        if dangling_only:
            cmd += ["--filter", "dangling=true"]
        result = self.runner.run(cmd)
        images: list[DockerImage] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            repo = raw.get("Repository", "<none>")
            tag = raw.get("Tag", "<none>")
            images.append(
                DockerImage(
                    id=raw.get("ID", ""),
                    repository=repo,
                    tag=tag,
                    size=_to_bytes(str(raw.get("Size", "0B"))),
                    dangling=repo == "<none>" or tag == "<none>",
                    created_at=str(raw.get("CreatedAt", "")),
                )
            )
        if not dangling_only:
            self._mark_stale(images)
        return images

    def container_image_refs(self) -> set[str]:
        """Every image reference held by a container, running or stopped.

        A container may name its image by ``repo:tag`` or by ID, so callers must
        check both forms.
        """
        result = self.runner.run(["docker", "ps", "--all", "--format", "{{.Image}}"])
        return {ref for ref in result.stdout.split() if ref}

    def _mark_stale(self, images: list[DockerImage]) -> None:
        """Flag images superseded by a newer tag of the same repository.

        An image is stale when nothing references it *and* a newer tag of the
        same repository is present. The newest tag of a repository is never
        stale even when unused — it is the one a fresh ``up`` would want.
        """
        refs = self.container_image_refs()
        by_repo: dict[str, list[DockerImage]] = {}
        for image in images:
            if image.dangling:
                continue
            by_repo.setdefault(image.repository, []).append(image)

        for repo, group in by_repo.items():
            if len(group) < 2:
                continue
            used = [img for img in group if _is_referenced(img, refs, repo)]
            keepers = {img.id for img in used}
            if not keepers:
                newest = max(group, key=lambda i: _created_sort_key(i.created_at))
                keepers = {newest.id}
            # ``latest`` is a moving pointer, not a pinned version — removing it
            # surprises anyone who runs the bare repo name.
            keepers |= {img.id for img in group if img.tag == "latest"}
            for image in group:
                image.stale = image.id not in keepers

    def stale_images(self) -> list[DockerImage]:
        """Images superseded by a newer version of the same repository."""
        return [image for image in self.images() if image.stale]

    def remove_images(self, references: list[str]) -> list[str]:
        """Remove images by reference. Honors the runner's dry-run mode.

        Returns:
            The references that were removed (or would be, under dry-run).
        """
        removed: list[str] = []
        for reference in references:
            result = self.runner.run(["docker", "rmi", reference], mutating=True, timeout=120)
            if result.ok:
                removed.append(reference)
        return removed

    def volumes(self) -> list[DockerVolume]:
        """List Docker volumes with in-use detection."""
        result = self.runner.run(["docker", "volume", "ls", "--format", "{{json .}}"])
        dangling = self._dangling_volume_names()
        volumes: list[DockerVolume] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            name = raw.get("Name", "")
            volumes.append(
                DockerVolume(
                    name=name,
                    driver=raw.get("Driver", "local"),
                    in_use=name not in dangling,
                )
            )
        return volumes

    def _dangling_volume_names(self) -> set[str]:
        result = self.runner.run(["docker", "volume", "ls", "--filter", "dangling=true", "-q"])
        return {n for n in result.stdout.split() if n}

    def reclaimable_estimate(
        self,
        usage: DockerUsage,
        *,
        all_images: bool = False,
        volumes: bool = False,
        build_cache: bool = True,
    ) -> int:
        """Estimate the bytes a prune with these options would actually free.

        ``usage.total_reclaimable`` is the ceiling — everything Docker considers
        unused. A safe prune cannot reach it: it only removes *dangling* images,
        leaving unused-but-tagged ones in place. Scoping the estimate to the
        selected options keeps the confirmation prompt honest.
        """
        total = usage.reclaimable_for("Containers")
        if all_images:
            total += usage.reclaimable_for("Images")
        else:
            total += sum(image.size for image in self.images(dangling_only=True))
        if volumes:
            total += usage.reclaimable_for("Local Volumes")
        if build_cache:
            total += usage.reclaimable_for("Build Cache")
        return total

    def prune(
        self,
        *,
        all_images: bool = False,
        volumes: bool = False,
        build_cache: bool = True,
    ) -> list[str]:
        """Run prune operations. Honors the runner's dry-run mode.

        Returns:
            The list of command descriptions that were run (or would run).
        """
        commands: list[list[str]] = []
        system_cmd = ["docker", "system", "prune", "--force"]
        if all_images:
            system_cmd.append("--all")
        if volumes:
            system_cmd.append("--volumes")
        commands.append(system_cmd)
        if build_cache:
            commands.append(["docker", "builder", "prune", "--force"])

        descriptions: list[str] = []
        for cmd in commands:
            self.runner.run(cmd, mutating=True, timeout=300)
            descriptions.append(" ".join(cmd))
        return descriptions
