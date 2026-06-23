"""Common models used across the application."""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field

__all__ = ["CommandResult", "HealthStatus", "ToolCheck"]


class HealthStatus(enum.StrEnum):
    """Coarse health classification rendered with color cues."""

    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    UNKNOWN = "unknown"

    @property
    def style(self) -> str:
        """Rich style name for this status."""
        return {
            HealthStatus.OK: "ok",
            HealthStatus.WARN: "warn",
            HealthStatus.ERROR: "err",
            HealthStatus.UNKNOWN: "muted",
        }[self]

    @property
    def icon(self) -> str:
        """Single-glyph indicator for this status."""
        return {
            HealthStatus.OK: "✓",
            HealthStatus.WARN: "!",
            HealthStatus.ERROR: "✗",
            HealthStatus.UNKNOWN: "?",
        }[self]


class CommandResult(BaseModel):
    """Result of running an external command."""

    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """True when the command exited successfully."""
        return self.returncode == 0


class ToolCheck(BaseModel):
    """Availability and version info for an external tool."""

    name: str
    available: bool
    version: str | None = None
    detail: str | None = None
    status: HealthStatus = Field(default=HealthStatus.UNKNOWN)
