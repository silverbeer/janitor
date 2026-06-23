"""Confirmation helpers honoring the global ``--yes`` flag."""

from __future__ import annotations

from rich.prompt import Confirm

from janitor.utils.console import console

__all__ = ["confirm"]


def confirm(message: str, *, assume_yes: bool = False, default: bool = False) -> bool:
    """Prompt for confirmation unless ``assume_yes`` short-circuits it.

    Args:
        message: The question to display.
        assume_yes: When True, return True without prompting.
        default: Default choice when the user just presses enter.
    """
    if assume_yes:
        console.print(f"[muted]{message} → yes (auto-confirmed)[/muted]")
        return True
    return Confirm.ask(message, default=default, console=console)
