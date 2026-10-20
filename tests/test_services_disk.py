"""Tests for the disk service."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from janitor.services.disk import DiskService, categorize


def test_usage() -> None:
    usage = DiskService().usage(Path("/"))
    assert usage.total > 0
    assert 0 <= usage.percent_used <= 100


def test_categorize() -> None:
    assert categorize(Path("/x/node_modules")) == "node_modules"
    assert categorize(Path("/x/.venv")) == "python-cache"
    assert categorize(Path("/x/target")) == "build-artifact"
    assert categorize(Path("/x/app.log")) == "logs"
    assert categorize(Path("/x/src")) is None


def test_categorize_multi_component_needle() -> None:
    assert categorize(Path("/Users/me/Library/Caches")) == "cache"
    assert categorize(Path("/Users/me/Library/Caches/Google")) == "cache"
    assert categorize(Path("/Users/me/Library")) is None
    assert categorize(Path("/Users/me/Caches")) is None


def test_categorize_npm_and_playwright() -> None:
    assert categorize(Path("/Users/me/.npm")) == "cache"
    assert categorize(Path("/Users/me/Library/Caches/ms-playwright")) == "cache"


def test_largest_files(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_bytes(b"x" * 10)
    (tmp_path / "big.bin").write_bytes(b"x" * 5000)
    (tmp_path / "mid.bin").write_bytes(b"x" * 1000)
    entries = DiskService().largest_files([tmp_path], top_n=2, min_size=0)
    assert len(entries) == 2
    assert entries[0].path.name == "big.bin"
    assert entries[0].size == (tmp_path / "big.bin").stat().st_blocks * 512


def test_largest_files_min_size(tmp_path: Path) -> None:
    (tmp_path / "tiny.txt").write_bytes(b"x" * 10)  # still one whole block on disk
    (tmp_path / "big.bin").write_bytes(b"x" * 64 * 1024)
    entries = DiskService().largest_files([tmp_path], min_size=32 * 1024)
    assert all(e.size >= 32 * 1024 for e in entries)
    assert [e.path.name for e in entries] == ["big.bin"]


def test_largest_dirs(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.bin").write_bytes(b"x" * 30000)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "f.bin").write_bytes(b"x" * 100)
    entries = DiskService().largest_dirs([tmp_path], min_size=0)
    assert entries[0].path.name == "a"
    assert entries[0].is_dir is True


def test_scan_skips_missing_root() -> None:
    entries = DiskService().largest_files([Path("/nonexistent-xyz")])
    assert entries == []


def test_sizes_are_allocated_not_apparent(tmp_path: Path) -> None:
    """A sparse file (Docker.raw-style) counts the bytes it stores, not its cap."""
    sparse = tmp_path / "vm" / "disk.raw"
    sparse.parent.mkdir()
    with sparse.open("wb") as fh:
        fh.truncate(1024**3)  # 1 GiB apparent, ~nothing allocated
    files = DiskService().largest_files([tmp_path])
    dirs = DiskService().largest_dirs([tmp_path])
    assert files[0].size < 1024**2
    assert dirs[0].size < 1024**2


def test_hardlinks_count_once(tmp_path: Path) -> None:
    (tmp_path / "cache").mkdir()
    original = tmp_path / "cache" / "a.bin"
    original.write_bytes(b"x" * 64 * 1024)
    os.link(original, tmp_path / "cache" / "b.bin")
    entries = DiskService().largest_dirs([tmp_path])
    assert entries[0].size == original.stat().st_blocks * 512


def _fake_mount(monkeypatch: pytest.MonkeyPatch, mount: Path) -> None:
    """Make ``mount`` report a different st_dev, as a real mount point would."""
    real_lstat, real_stat = os.lstat, Path.stat

    def shift(st: os.stat_result) -> os.stat_result:
        fields = list(st)
        fields[2] = st.st_dev + 1  # index 2 is st_dev
        return os.stat_result(fields)

    def fake_lstat(path: str | os.PathLike[str], *a: object, **kw: object) -> os.stat_result:
        st = real_lstat(path)
        return shift(st) if Path(path) == mount else st

    def fake_stat(self: Path, *a: object, **kw: object) -> os.stat_result:
        st = real_stat(self)
        return shift(st) if self == mount else st

    monkeypatch.setattr(os, "lstat", fake_lstat)
    monkeypatch.setattr(Path, "stat", fake_stat)


def test_scan_does_not_cross_mounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A mount under the root (e.g. CoreDevice/DeviceFS, an iPhone) is not local usage."""
    dev = tmp_path / "Developer"
    (dev / "DeviceFS").mkdir(parents=True)
    (dev / "DeviceFS" / "phone.bin").write_bytes(b"x" * 64 * 1024)
    (dev / "local.bin").write_bytes(b"x" * 8 * 1024)
    _fake_mount(monkeypatch, dev / "DeviceFS")

    files = DiskService().largest_files([tmp_path])
    assert [e.path.name for e in files] == ["local.bin"]
    dirs = DiskService().largest_dirs([tmp_path])
    assert dirs[0].size == (dev / "local.bin").lstat().st_blocks * 512


def test_largest_dirs_skips_mounted_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "DeviceFS").mkdir()
    (tmp_path / "DeviceFS" / "phone.bin").write_bytes(b"x" * 64 * 1024)
    (tmp_path / "local").mkdir()
    _fake_mount(monkeypatch, tmp_path / "DeviceFS")
    assert [e.path.name for e in DiskService().largest_dirs([tmp_path])] == ["local"]


def test_largest_dirs_skips_symlinked_child(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "f.bin").write_bytes(b"x" * 4096)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(target)
    assert DiskService().largest_dirs([root]) == []
