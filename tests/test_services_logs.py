"""Tests for the logs service."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

from janitor.services.logs import LogsService


def _make_log(path: Path, *, size: int, age_days: int) -> None:
    path.write_bytes(b"x" * size)
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))


def test_find_filters_by_size(tmp_path: Path) -> None:
    _make_log(tmp_path / "big.log", size=5000, age_days=1)
    _make_log(tmp_path / "small.log", size=10, age_days=1)
    (tmp_path / "notalog.txt").write_bytes(b"x" * 5000)
    found = LogsService().find([tmp_path], min_size=1000)
    names = {f.path.name for f in found}
    assert names == {"big.log"}


def test_find_computes_age(tmp_path: Path) -> None:
    _make_log(tmp_path / "old.log", size=100, age_days=10)
    now = datetime.now(UTC)
    found = LogsService().find([tmp_path], now=now)
    assert found[0].age_days >= 9


def test_clean_respects_age(tmp_path: Path) -> None:
    _make_log(tmp_path / "old.log", size=100, age_days=40)
    _make_log(tmp_path / "new.log", size=100, age_days=1)
    service = LogsService()
    logs = service.find([tmp_path])
    removed = service.clean(logs, max_age_days=30)
    assert {r.path.name for r in removed} == {"old.log"}
    assert not (tmp_path / "old.log").exists()
    assert (tmp_path / "new.log").exists()


def test_clean_dry_run_keeps_files(tmp_path: Path) -> None:
    _make_log(tmp_path / "old.log", size=100, age_days=40)
    service = LogsService(dry_run=True)
    logs = service.find([tmp_path])
    removed = service.clean(logs, max_age_days=30)
    assert len(removed) == 1
    assert (tmp_path / "old.log").exists()


def test_find_missing_path() -> None:
    assert LogsService().find([Path("/nonexistent-xyz")]) == []
