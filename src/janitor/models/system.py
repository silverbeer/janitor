"""Models for Homebrew, logs, and Supabase."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

__all__ = ["BrewOutdated", "BrewStatus", "LogFile", "SupabaseProject"]


class BrewOutdated(BaseModel):
    """An outdated Homebrew formula or cask."""

    name: str
    current_version: str
    latest_version: str
    is_cask: bool = False


class BrewStatus(BaseModel):
    """Summary of Homebrew state."""

    available: bool
    prefix: str | None = None
    outdated: list[BrewOutdated] = Field(default_factory=list)

    @property
    def outdated_count(self) -> int:
        """Number of outdated packages."""
        return len(self.outdated)


class LogFile(BaseModel):
    """A discovered log file."""

    path: Path
    size: int
    mtime: float
    age_days: int


class SupabaseProject(BaseModel):
    """A local Supabase project."""

    path: Path
    name: str
    running: bool = False
    detail: str | None = None
