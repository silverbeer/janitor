"""Tests for the disk service."""

from __future__ import annotations

from pathlib import Path

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


def test_largest_files(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_bytes(b"x" * 10)
    (tmp_path / "big.bin").write_bytes(b"x" * 5000)
    (tmp_path / "mid.bin").write_bytes(b"x" * 1000)
    entries = DiskService().largest_files([tmp_path], top_n=2, min_size=0)
    assert len(entries) == 2
    assert entries[0].path.name == "big.bin"
    assert entries[0].size == 5000


def test_largest_files_min_size(tmp_path: Path) -> None:
    (tmp_path / "tiny.txt").write_bytes(b"x" * 10)
    (tmp_path / "big.bin").write_bytes(b"x" * 5000)
    entries = DiskService().largest_files([tmp_path], min_size=1000)
    assert all(e.size >= 1000 for e in entries)
    assert len(entries) == 1


def test_largest_dirs(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.bin").write_bytes(b"x" * 3000)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "f.bin").write_bytes(b"x" * 100)
    entries = DiskService().largest_dirs([tmp_path], min_size=0)
    assert entries[0].path.name == "a"
    assert entries[0].is_dir is True


def test_scan_skips_missing_root() -> None:
    entries = DiskService().largest_files([Path("/nonexistent-xyz")])
    assert entries == []
