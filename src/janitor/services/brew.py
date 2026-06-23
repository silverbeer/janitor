"""Homebrew service."""

from __future__ import annotations

import json

from janitor.models.system import BrewOutdated, BrewStatus
from janitor.services.shell import ShellRunner, which

__all__ = ["BrewService"]


class BrewService:
    """Wrapper around the ``brew`` CLI."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self.runner = runner or ShellRunner()

    def is_available(self) -> bool:
        """True when Homebrew is installed."""
        return which("brew") is not None

    def status(self) -> BrewStatus:
        """Return Homebrew prefix and outdated packages."""
        if not self.is_available():
            return BrewStatus(available=False)
        prefix = self.runner.capture(["brew", "--prefix"]) or None
        return BrewStatus(
            available=True,
            prefix=prefix,
            outdated=self._outdated(),
        )

    def _outdated(self) -> list[BrewOutdated]:
        result = self.runner.run(["brew", "outdated", "--json=v2"])
        if not result.ok or not result.stdout.strip():
            return []
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []
        items: list[BrewOutdated] = []
        for formula in data.get("formulae", []):
            installed = formula.get("installed_versions") or ["?"]
            items.append(
                BrewOutdated(
                    name=formula.get("name", "?"),
                    current_version=installed[0],
                    latest_version=formula.get("current_version", "?"),
                    is_cask=False,
                )
            )
        for cask in data.get("casks", []):
            installed = cask.get("installed_versions") or [cask.get("installed", "?")]
            items.append(
                BrewOutdated(
                    name=cask.get("name", "?"),
                    current_version=installed[0] if installed else "?",
                    latest_version=cask.get("current_version", "?"),
                    is_cask=True,
                )
            )
        return items

    def upgrade(self) -> str:
        """Upgrade all outdated packages. Honors dry-run."""
        result = self.runner.run(["brew", "upgrade"], mutating=True, timeout=1800)
        return result.stdout or result.stderr

    def cleanup(self) -> str:
        """Remove old versions and clear the download cache. Honors dry-run."""
        result = self.runner.run(["brew", "cleanup", "--prune=all"], mutating=True, timeout=600)
        return result.stdout or result.stderr
