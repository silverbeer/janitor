"""Tests for formatting utilities."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from janitor.utils.format import format_age, format_bytes, parse_size


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024**2, "1.0 MB"),
        (1024**3, "1.0 GB"),
        (1024**4, "1.0 TB"),
    ],
)
def test_format_bytes(value: int, expected: str) -> None:
    assert format_bytes(value) == expected


def test_format_bytes_negative() -> None:
    assert format_bytes(-1024) == "-1.0 KB"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1024", 1024),
        ("1KB", 1024),
        ("1.5GB", int(1.5 * 1024**3)),
        ("500M", 500 * 1024**2),
        ("2 GiB", 2 * 1024**3),
        ("3T", 3 * 1024**4),
    ],
)
def test_parse_size(text: str, expected: int) -> None:
    assert parse_size(text) == expected


def test_parse_size_invalid() -> None:
    with pytest.raises(ValueError, match="Cannot parse"):
        parse_size("not-a-size")


def test_format_age() -> None:
    now = datetime(2026, 1, 10, tzinfo=UTC)
    five_days_ago = datetime(2026, 1, 5, tzinfo=UTC).timestamp()
    assert format_age(five_days_ago, now=now) == "5d"


def test_format_age_units() -> None:
    now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
    assert format_age(now.timestamp() - 30, now=now) == "30s"
    assert format_age(now.timestamp() - 120, now=now) == "2m"
    assert format_age(now.timestamp() - 7200, now=now) == "2h"
    assert format_age(now.timestamp() + 100, now=now) == "future"
