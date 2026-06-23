"""Disk service: usage stats and large file/directory discovery."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from janitor.models.disk import DiskUsage, FileEntry

__all__ = ["DiskService", "categorize"]

#: Directory-name → category for highlighting common offenders.
_CATEGORY_DIRS: dict[str, str] = {
    "node_modules": "node_modules",
    ".venv": "python-cache",
    "venv": "python-cache",
    "__pycache__": "python-cache",
    ".mypy_cache": "python-cache",
    ".pytest_cache": "python-cache",
    ".ruff_cache": "python-cache",
    "Downloads": "downloads",
    "target": "build-artifact",
    "dist": "build-artifact",
    "build": "build-artifact",
    ".next": "build-artifact",
    ".gradle": "build-artifact",
    "Library/Caches": "cache",
    ".cache": "cache",
    ".docker": "docker",
    "Logs": "logs",
}


def categorize(path: Path) -> str | None:
    """Classify ``path`` into a known offender category, if any."""
    parts = set(path.parts)
    for needle, category in _CATEGORY_DIRS.items():
        if needle in parts or path.name == needle:
            return category
    if path.suffix in {".log"}:
        return "logs"
    return None


class DiskService:
    """Inspect disk usage and scan for large files and directories."""

    def usage(self, path: Path | None = None) -> DiskUsage:
        """Return filesystem usage for ``path`` (defaults to root)."""
        target = path or Path("/")
        total, used, free = shutil.disk_usage(target)
        return DiskUsage(path=target, total=total, used=used, free=free)

    def largest_files(
        self,
        roots: list[Path],
        *,
        top_n: int = 20,
        min_size: int = 0,
    ) -> list[FileEntry]:
        """Walk ``roots`` and return the largest files by size."""
        entries: list[FileEntry] = []
        for root in roots:
            for file_path, size in self._iter_files(root, min_size=min_size):
                entries.append(
                    FileEntry(
                        path=file_path,
                        size=size,
                        is_dir=False,
                        category=categorize(file_path),
                    )
                )
        entries.sort(key=lambda e: e.size, reverse=True)
        return entries[:top_n]

    def largest_dirs(
        self,
        roots: list[Path],
        *,
        top_n: int = 20,
        min_size: int = 0,
    ) -> list[FileEntry]:
        """Return the largest immediate subdirectories under ``roots``."""
        entries: list[FileEntry] = []
        for root in roots:
            if not root.is_dir():
                continue
            for child in self._safe_iterdir(root):
                if not child.is_dir():
                    continue
                size = self._dir_size(child)
                if size < min_size:
                    continue
                entries.append(
                    FileEntry(
                        path=child,
                        size=size,
                        is_dir=True,
                        category=categorize(child),
                    )
                )
        entries.sort(key=lambda e: e.size, reverse=True)
        return entries[:top_n]

    @staticmethod
    def _safe_iterdir(path: Path) -> list[Path]:
        try:
            return list(path.iterdir())
        except PermissionError, OSError:
            return []

    def _iter_files(self, root: Path, *, min_size: int) -> list[tuple[Path, int]]:
        found: list[tuple[Path, int]] = []
        if not root.exists():
            return found
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Skip into VCS internals which are rarely actionable.
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                fp = Path(dirpath) / name
                try:
                    stat = fp.lstat()
                except OSError, ValueError:
                    continue
                if stat.st_size >= min_size:
                    found.append((fp, stat.st_size))
        return found

    def _dir_size(self, root: Path) -> int:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                try:
                    total += (Path(dirpath) / name).lstat().st_size
                except OSError, ValueError:
                    continue
        return total
