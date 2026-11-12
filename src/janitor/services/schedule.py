"""launchd scheduling for recurring `jt` jobs on macOS (SB-1580).

One LaunchAgent per job, in the user's GUI domain so it can reach the login
keychain (``gh`` keeps its token there). The plist is generated, never edited by
hand: ``jt git schedule install`` rewrites it and reloads it.
"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

from janitor.services.shell import ShellRunner

__all__ = ["LaunchAgent", "build_plist"]

#: launchd starts jobs with a bare PATH; git, gh, du and jt all need to resolve.
_PATH = ":".join(
    [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
)


def build_plist(
    *,
    label: str,
    program: list[str],
    hour: int,
    minute: int,
    log_file: Path,
    weekday: int | None = None,
) -> bytes:
    """The LaunchAgent plist for a job at ``hour:minute`` local time.

    Daily by default; ``weekday`` (launchd numbering, 0 = Sunday) makes it weekly.
    """
    interval = {"Hour": hour, "Minute": minute}
    if weekday is not None:
        interval["Weekday"] = weekday
    return plistlib.dumps(
        {
            "Label": label,
            "ProgramArguments": program,
            "StartCalendarInterval": interval,
            "EnvironmentVariables": {"PATH": _PATH, "HOME": str(Path.home())},
            "StandardOutPath": str(log_file),
            "StandardErrorPath": str(log_file),
            "RunAtLoad": False,
            "ProcessType": "Background",
            "LowPriorityIO": True,
        }
    )


class LaunchAgent:
    """Install, inspect and remove one LaunchAgent."""

    def __init__(self, runner: ShellRunner, label: str, *, agents_dir: Path | None = None) -> None:
        self.runner = runner
        self.label = label
        self.agents_dir = agents_dir or Path.home() / "Library" / "LaunchAgents"

    @property
    def plist_path(self) -> Path:
        """Where the plist lives."""
        return self.agents_dir / f"{self.label}.plist"

    @property
    def _domain(self) -> str:
        return f"gui/{os.getuid()}"

    def install(self, plist: bytes, *, log_file: Path) -> bool:
        """Write the plist and (re)load it. True when launchd accepted it."""
        if self.runner.dry_run:
            return True
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        self.plist_path.write_bytes(plist)
        # bootout fails when it was never loaded — expected, ignored.
        self.runner.run(["launchctl", "bootout", f"{self._domain}/{self.label}"], mutating=True)
        result = self.runner.run(
            ["launchctl", "bootstrap", self._domain, str(self.plist_path)], mutating=True
        )
        return result.ok

    def loaded(self) -> bool:
        """Whether launchd currently has the job."""
        return self.runner.run(["launchctl", "print", f"{self._domain}/{self.label}"]).ok

    def uninstall(self) -> bool:
        """Unload and delete. True when something was removed."""
        existed = self.plist_path.exists()
        self.runner.run(["launchctl", "bootout", f"{self._domain}/{self.label}"], mutating=True)
        if existed and not self.runner.dry_run:
            self.plist_path.unlink()
        return existed
