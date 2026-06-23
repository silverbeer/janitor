"""Disk-related models."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

__all__ = ["DiskUsage", "FileEntry"]


class DiskUsage(BaseModel):
    """Filesystem usage for a mount point."""

    path: Path
    total: int
    used: int
    free: int

    @property
    def percent_used(self) -> float:
        """Percentage of the filesystem in use, 0 to 100."""
        if self.total == 0:
            return 0.0
        return round(self.used / self.total * 100, 1)


class FileEntry(BaseModel):
    """A file or directory discovered during a disk scan."""

    path: Path
    size: int
    is_dir: bool = False
    category: str | None = None
