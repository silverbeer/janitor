"""Shared Rich console instances."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

__all__ = ["console", "err_console"]

_theme = Theme(
    {
        "ok": "bold green",
        "warn": "bold yellow",
        "err": "bold red",
        "info": "cyan",
        "muted": "dim",
        "accent": "bold magenta",
        "heading": "bold cyan",
    }
)

#: Primary console for user-facing output (stdout).
console = Console(theme=_theme, highlight=False)

#: Console for errors and diagnostics (stderr).
err_console = Console(theme=_theme, stderr=True, highlight=False)
