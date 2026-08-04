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

    def record(self, type_: str) -> DockerUsageRecord | None:
        """Return the record for a ``docker system df`` type, if present.

        Matching is case-insensitive and ignores spaces, so ``"local volumes"``
        and ``"LocalVolumes"`` both find the ``Local Volumes`` row.
        """
        wanted = type_.replace(" ", "").casefold()
        for record in self.records:
            if record.type.replace(" ", "").casefold() == wanted:
                return record
        return None

    def reclaimable_for(self, type_: str) -> int:
        """Reclaimable bytes for one record type, or 0 when absent."""
        record = self.record(type_)
        return record.reclaimable if record else 0


class DockerImage(BaseModel):
    """A Docker image entry."""

    id: str
    repository: str = "<none>"
    tag: str = "<none>"
    size: int = 0
    dangling: bool = False
    created_at: str = ""
    stale: bool = False
    """True when a newer tag of the same repository exists and nothing uses this one."""

    @property
    def reference(self) -> str:
        """The ``repository:tag`` reference used to address this image."""
        return f"{self.repository}:{self.tag}"


class DockerVolume(BaseModel):
    """A Docker volume entry."""

    name: str
    driver: str = "local"
    size: int = 0
    in_use: bool = False
