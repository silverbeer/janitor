"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import pytest

from janitor.models.common import CommandResult
from janitor.services.shell import ShellRunner


class FakeRunner(ShellRunner):
    """A ShellRunner that returns canned results keyed by command prefix.

    Register responses with :meth:`stub`. The longest matching prefix wins.
    """

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._stubs: list[tuple[tuple[str, ...], CommandResult]] = []
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def stub(
        self,
        prefix: Sequence[str],
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> None:
        result = CommandResult(
            command=list(prefix),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
        self._stubs.append((tuple(prefix), result))
        # Longest prefix first for specificity.
        self._stubs.sort(key=lambda item: len(item[0]), reverse=True)

    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
        timeout: float | None = 60.0,
        mutating: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        cmd = list(command)
        self.calls.append(cmd)
        if env is not None:
            self.envs.append(dict(env))
        if mutating and self.dry_run:
            return CommandResult(command=cmd, returncode=0)
        for prefix, result in self._stubs:
            if tuple(cmd[: len(prefix)]) == prefix:
                return result.model_copy(update={"command": cmd})
        return CommandResult(command=cmd, returncode=0, stdout="", stderr="")


@pytest.fixture
def fake_runner() -> FakeRunner:
    """Return a fresh FakeRunner."""
    return FakeRunner()


@pytest.fixture
def make_runner() -> Callable[..., FakeRunner]:
    """Return a factory for FakeRunner instances (e.g. dry_run variants)."""

    def factory(*, dry_run: bool = False) -> FakeRunner:
        return FakeRunner(dry_run=dry_run)

    return factory
