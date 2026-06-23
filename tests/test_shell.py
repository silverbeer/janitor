"""Tests for the ShellRunner subprocess wrapper."""

from __future__ import annotations

import subprocess

import pytest

from janitor.services.shell import CommandError, ShellRunner, which


def test_run_success() -> None:
    runner = ShellRunner()
    result = runner.run(["python3", "-c", "print('hi')"])
    assert result.ok
    assert result.stdout.strip() == "hi"


def test_run_nonzero() -> None:
    runner = ShellRunner()
    result = runner.run(["python3", "-c", "import sys; sys.exit(3)"])
    assert result.returncode == 3
    assert not result.ok


def test_run_check_raises() -> None:
    runner = ShellRunner()
    with pytest.raises(CommandError):
        runner.run(["python3", "-c", "import sys; sys.exit(1)"], check=True)


def test_run_missing_binary() -> None:
    runner = ShellRunner()
    result = runner.run(["definitely-not-a-real-binary-xyz"])
    assert result.returncode == 127


def test_run_missing_binary_check() -> None:
    runner = ShellRunner()
    with pytest.raises(CommandError):
        runner.run(["definitely-not-a-real-binary-xyz"], check=True)


def test_dry_run_skips_mutating() -> None:
    runner = ShellRunner(dry_run=True)
    result = runner.run(["rm", "-rf", "/tmp/should-not-run"], mutating=True)
    assert result.ok
    assert result.stdout == ""


def test_dry_run_allows_readonly() -> None:
    runner = ShellRunner(dry_run=True)
    result = runner.run(["python3", "-c", "print('read')"])
    assert result.stdout.strip() == "read"


def test_capture() -> None:
    runner = ShellRunner()
    assert runner.capture(["python3", "-c", "print('x')"]) == "x"


def test_capture_failure_returns_empty() -> None:
    runner = ShellRunner()
    assert runner.capture(["python3", "-c", "import sys; sys.exit(1)"]) == ""


def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = ShellRunner()

    def boom(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="x", timeout=0.1)

    monkeypatch.setattr(subprocess, "run", boom)
    result = runner.run(["sleep", "10"], timeout=0.1)
    assert result.returncode == 124


def test_which() -> None:
    assert which("python3") is not None
    assert which("definitely-not-a-real-binary-xyz") is None
