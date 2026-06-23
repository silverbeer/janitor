"""Shared application state passed through Typer's context object."""

from __future__ import annotations

from dataclasses import dataclass

from janitor.config import JanitorConfig
from janitor.services.shell import ShellRunner

__all__ = ["AppState"]


@dataclass
class AppState:
    """Runtime state available to every command via ``ctx.obj``."""

    config: JanitorConfig
    runner: ShellRunner

    @property
    def dry_run(self) -> bool:
        """Whether mutating actions should be previewed only."""
        return self.config.dry_run

    @property
    def assume_yes(self) -> bool:
        """Whether confirmation prompts should be auto-accepted."""
        return self.config.assume_yes
