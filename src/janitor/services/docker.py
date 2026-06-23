"""Docker service: parse usage and perform cleanup."""

from __future__ import annotations

import json

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
                )
            )
        return images

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
