"""Subprocess execution wrapper.

All external commands flow through :class:`ShellRunner` so that tests can mock a
single seam and so that dry-run / logging behavior is centralized.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence

from janitor.logging import get_logger
from janitor.models.common import CommandResult

__all__ = ["CommandError", "ShellRunner", "which"]

logger = get_logger(__name__)


def which(executable: str) -> str | None:
    """Return the resolved path to ``executable`` if present on ``PATH``."""
    return shutil.which(executable)


class CommandError(RuntimeError):
    """Raised when a required command fails and ``check=True``."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        joined = " ".join(result.command)
        super().__init__(f"Command failed ({result.returncode}): {joined}\n{result.stderr.strip()}")


class ShellRunner:
    """Run external commands with optional dry-run support.

    Args:
        dry_run: When True, mutating commands are logged but not executed.
    """

    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
        timeout: float | None = 60.0,
        mutating: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Execute ``command`` and return a :class:`CommandResult`.

        Args:
            command: Argument vector (never passed through a shell).
            check: Raise :class:`CommandError` on non-zero exit.
            timeout: Seconds before the command is killed.
            mutating: Marks the command as state-changing; skipped during dry-run.
            env: Extra environment variables merged over the current environment.
                Use this to pass secrets (e.g. ``PGPASSWORD``) so they never
                appear in the argument vector or the command logs.
        """
        cmd = list(command)
        if mutating and self.dry_run:
            logger.info("dry_run.skip", command=cmd)
            return CommandResult(command=cmd, returncode=0, stdout="", stderr="")

        # ``env`` is intentionally omitted from the log — it carries secrets.
        logger.debug("shell.run", command=cmd)
        child_env = {**os.environ, **env} if env else None
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=child_env,
            )
        except FileNotFoundError as exc:
            result = CommandResult(command=cmd, returncode=127, stderr=str(exc))
            if check:
                raise CommandError(result) from exc
            return result
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(command=cmd, returncode=124, stderr=f"timeout: {exc}")
            if check:
                raise CommandError(result) from exc
            return result

        result = CommandResult(
            command=cmd,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and not result.ok:
            raise CommandError(result)
        return result

    def capture(self, command: Sequence[str], *, timeout: float | None = 60.0) -> str:
        """Run ``command`` and return stripped stdout, or ``""`` on failure."""
        result = self.run(command, timeout=timeout)
        return result.stdout.strip() if result.ok else ""

    def exec_passthrough(
        self, command: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> int:
        """Run ``command`` inheriting the parent's stdio and return its exit code.

        Unlike :meth:`run`, output is NOT captured — the child owns the terminal.
        Use this to wrap interactive commands (e.g. a tool that prompts for
        Touch ID). Honors dry-run by skipping execution and returning 0.
        """
        cmd = list(command)
        if self.dry_run:
            logger.info("dry_run.exec", command=cmd)
            return 0
        logger.debug("shell.exec", command=cmd)
        child_env = {**os.environ, **env} if env else None
        completed = subprocess.run(cmd, env=child_env, check=False)  # stdio inherited
        return completed.returncode
