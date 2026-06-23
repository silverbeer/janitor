"""Docker-related models."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["DockerImage", "DockerUsage", "DockerVolume"]


class DockerUsageRecord(BaseModel):
    """A single row from ``docker system df`` (the type-level summary)."""

    type: str
    total: int = 0
    active: int = 0
    size: int = 0
    reclaimable: int = 0


class DockerUsage(BaseModel):
    """Parsed ``docker system df`` output."""

    records: list[DockerUsageRecord] = Field(default_factory=list)

    @property
    def total_reclaimable(self) -> int:
        """Total reclaimable bytes across all record types."""
        return sum(r.reclaimable for r in self.records)

    @property
    def total_size(self) -> int:
        """Total bytes used across all record types."""
        return sum(r.size for r in self.records)


class DockerImage(BaseModel):
    """A Docker image entry."""

    id: str
    repository: str = "<none>"
    tag: str = "<none>"
    size: int = 0
    dangling: bool = False


class DockerVolume(BaseModel):
    """A Docker volume entry."""

    name: str
    driver: str = "local"
    size: int = 0
    in_use: bool = False
