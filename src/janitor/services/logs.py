"""Log discovery and cleanup service."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from janitor.logging import get_logger
from janitor.models.system import LogFile

__all__ = ["LogsService"]

logger = get_logger(__name__)

_LOG_SUFFIXES = (".log", ".log.gz", ".out")


class LogsService:
    """Find and clean up log files."""

    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def find(
        self,
        paths: list[Path],
        *,
        min_size: int = 0,
        now: datetime | None = None,
    ) -> list[LogFile]:
        """Return log files under ``paths`` at or above ``min_size``."""
        reference = now or datetime.now(UTC)
        found: list[LogFile] = []
        for base in paths:
            if not base.exists():
                continue
            for dirpath, _dirs, files in os.walk(base, followlinks=False):
                for name in files:
                    if not name.endswith(_LOG_SUFFIXES):
                        continue
                    fp = Path(dirpath) / name
                    try:
                        stat = fp.lstat()
                    except OSError, ValueError:
                        continue
                    if stat.st_size < min_size:
                        continue
                    age = (reference.timestamp() - stat.st_mtime) / 86400
                    found.append(
                        LogFile(
                            path=fp,
                            size=stat.st_size,
                            mtime=stat.st_mtime,
                            age_days=int(age),
                        )
                    )
        found.sort(key=lambda f: f.size, reverse=True)
        return found

    def clean(self, logs: list[LogFile], *, max_age_days: int) -> list[LogFile]:
        """Delete logs older than ``max_age_days``. Honors dry-run.

        Returns:
            The list of log files that were (or would be) removed.
        """
        removed: list[LogFile] = []
        for log in logs:
            if log.age_days < max_age_days:
                continue
            removed.append(log)
            if self.dry_run:
                logger.info("logs.dry_run.remove", path=str(log.path))
                continue
            try:
                log.path.unlink()
                logger.info("logs.removed", path=str(log.path), bytes=log.size)
            except OSError as exc:  # pragma: no cover - filesystem dependent
                logger.warning("logs.remove_failed", path=str(log.path), error=str(exc))
        return removed
