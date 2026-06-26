"""Models for Homebrew, logs, and Supabase."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

__all__ = [
    "AdminUser",
    "BackupDirReport",
    "BackupFile",
    "BrewOutdated",
    "BrewStatus",
    "LogFile",
    "RestoreResult",
    "SupabaseProject",
    "UserSyncResult",
]


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


class BackupFile(BaseModel):
    """A single database backup dump on disk."""

    path: Path
    size: int
    mtime: float
    age_days: int


class BackupDirReport(BaseModel):
    """Health of a project's backup directory: size, retention, growth."""

    project: str
    directory: Path
    files: list[BackupFile] = Field(default_factory=list)
    total_size: int = 0
    #: Configured ceilings (0 == unlimited / disabled).
    max_size: int = 0
    retention_count: int = 0
    retention_days: int = 0
    #: Files rotation would (or did) remove to satisfy retention.
    prunable: list[BackupFile] = Field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of backup files present."""
        return len(self.files)

    @property
    def over_size(self) -> bool:
        """True when the directory exceeds its configured size ceiling."""
        return self.max_size > 0 and self.total_size > self.max_size

    @property
    def over_count(self) -> bool:
        """True when more backups are retained than the count ceiling allows."""
        return self.retention_count > 0 and self.count > self.retention_count

    @property
    def healthy(self) -> bool:
        """True when neither the size nor retention ceilings are breached."""
        return not (self.over_size or self.over_count or self.prunable)


class RestoreResult(BaseModel):
    """Outcome of restoring prod data into a local Supabase database."""

    project: str
    reset: bool = False
    dump_path: Path
    dumped_bytes: int = 0
    loaded: bool = False
    dry_run: bool = False


class AdminUser(BaseModel):
    """An auth user as seen through the Supabase Admin API."""

    id: str
    email: str
    role: str | None = None
    user_metadata: dict[str, object] = Field(default_factory=dict)


class UserSyncResult(BaseModel):
    """Outcome of syncing a single auth user into the local database."""

    email: str
    user_id: str
    password: str
    #: One of: ``"synced"``, ``"skipped"``, ``"failed"``.
    action: str
    detail: str | None = None
    dry_run: bool = False
