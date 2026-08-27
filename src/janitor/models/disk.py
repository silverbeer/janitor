"""Disk-related models."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from janitor.models.common import HealthStatus

__all__ = ["DEFAULT_DISK_ERROR_PERCENT", "DEFAULT_DISK_WARN_PERCENT", "DiskUsage", "FileEntry"]

# Defaults for classifying filesystem pressure. A machine with different
# headroom can override them via DiskConfig.
DEFAULT_DISK_WARN_PERCENT = 75.0
DEFAULT_DISK_ERROR_PERCENT = 90.0


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

    def health(
        self,
        warn_percent: float = DEFAULT_DISK_WARN_PERCENT,
        error_percent: float = DEFAULT_DISK_ERROR_PERCENT,
    ) -> HealthStatus:
        """How worried to be about this filesystem.

        Lives on the model rather than in the command because the caller that
        renders a row and the caller that decides an overall verdict must not be
        able to disagree. They did: `jt doctor` coloured the disk row red at
        >=90% while its summary still read "All systems healthy", because the
        row's threshold was a local variable the verdict never saw.
        """
        if self.percent_used >= error_percent:
            return HealthStatus.ERROR
        if self.percent_used >= warn_percent:
            return HealthStatus.WARN
        return HealthStatus.OK


class FileEntry(BaseModel):
    """A file or directory discovered during a disk scan."""

    path: Path
    size: int
    is_dir: bool = False
    category: str | None = None
