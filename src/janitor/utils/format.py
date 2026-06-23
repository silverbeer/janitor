"""Human-readable formatting helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime

__all__ = ["format_age", "format_bytes", "parse_size"]

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([kKmMgGtTpP]?)([iI]?)[bB]?\s*$")
_MULTIPLIERS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4, "p": 1024**5}


def format_bytes(num: float, *, precision: int = 1) -> str:
    """Format a byte count as a human-readable string (binary units).

    >>> format_bytes(0)
    '0 B'
    >>> format_bytes(1536)
    '1.5 KB'
    """
    value = float(num)
    if value < 0:
        return f"-{format_bytes(-value, precision=precision)}"
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.{precision}f} {unit}"
        value /= 1024
    return f"{value:.{precision}f} {_UNITS[-1]}"  # pragma: no cover


def parse_size(text: str) -> int:
    """Parse a human size string (e.g. ``"1.5GB"``, ``"500M"``) into bytes.

    Raises:
        ValueError: If the string cannot be parsed.
    """
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"Cannot parse size: {text!r}")
    amount, unit, _ = match.groups()
    return int(float(amount) * _MULTIPLIERS[unit.lower()])


def format_age(timestamp: float, *, now: datetime | None = None) -> str:
    """Format a POSIX timestamp as a coarse age string (e.g. ``"5d"``)."""
    reference = now or datetime.now(UTC)
    moment = datetime.fromtimestamp(timestamp, tz=UTC)
    delta = reference - moment
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "future"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
