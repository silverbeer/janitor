"""Disk service: usage stats and large file/directory discovery."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from janitor.models.disk import DiskUsage, FileEntry

__all__ = ["DiskService", "categorize"]

#: ``st_blocks`` is always in 512-byte units, whatever the filesystem block size.
_BLOCK_SIZE = 512

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
    ".npm": "cache",
    "ms-playwright": "cache",
    ".docker": "docker",
    "Logs": "logs",
}


def categorize(path: Path) -> str | None:
    """Classify ``path`` into a known offender category, if any."""
    parts = path.parts
    for needle, category in _CATEGORY_DIRS.items():
        # Multi-component needles ("Library/Caches") must match as a contiguous run.
        seq = tuple(needle.split("/"))
        if any(parts[i : i + len(seq)] == seq for i in range(len(parts) - len(seq) + 1)):
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
            root_dev = self._device(root)
            for child in self._safe_iterdir(root):
                if child.is_symlink() or not child.is_dir():
                    continue
                if self._device(child) != root_dev:
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

    @staticmethod
    def _device(path: Path) -> int | None:
        try:
            return path.stat().st_dev
        except OSError:
            return None

    @staticmethod
    def _allocated(stat: os.stat_result) -> int:
        """Bytes actually on disk — a sparse VM image counts what it stores, not its cap."""
        return stat.st_blocks * _BLOCK_SIZE

    @staticmethod
    def _walk_one_fs(root: Path, root_dev: int) -> Iterator[tuple[str, list[str]]]:
        """``os.walk`` that, like ``du -x``, never descends into another mount.

        A mount under the scan root is not local usage, and may not even be local:
        ``~/Library/Developer/CoreDevice/DeviceFS`` is a live view of an iPhone.
        """
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            kept = []
            for d in dirnames:
                if d == ".git":
                    continue
                try:
                    if os.lstat(os.path.join(dirpath, d)).st_dev != root_dev:
                        continue
                except OSError:
                    continue
                kept.append(d)
            dirnames[:] = kept
            yield dirpath, filenames

    def _iter_files(self, root: Path, *, min_size: int) -> list[tuple[Path, int]]:
        found: list[tuple[Path, int]] = []
        root_dev = self._device(root)
        if root_dev is None:
            return found
        for dirpath, filenames in self._walk_one_fs(root, root_dev):
            for name in filenames:
                fp = Path(dirpath) / name
                try:
                    stat = fp.lstat()
                except OSError, ValueError:
                    continue
                size = self._allocated(stat)
                if size >= min_size:
                    found.append((fp, size))
        return found

    def _dir_size(self, root: Path) -> int:
        root_dev = self._device(root)
        if root_dev is None:
            return 0
        total = 0
        seen: set[int] = set()
        for dirpath, filenames in self._walk_one_fs(root, root_dev):
            for name in filenames:
                try:
                    stat = (Path(dirpath) / name).lstat()
                except OSError, ValueError:
                    continue
                # Hardlinked files (uv's cache is full of them) count once, as du does.
                if stat.st_nlink > 1:
                    if stat.st_ino in seen:
                        continue
                    seen.add(stat.st_ino)
                total += self._allocated(stat)
        return total
