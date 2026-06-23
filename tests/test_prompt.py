"""Tests for the confirmation helper."""

from __future__ import annotations

import pytest

from janitor.utils import prompt


def test_confirm_assume_yes() -> None:
    assert prompt.confirm("Proceed?", assume_yes=True) is True


def test_confirm_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt.Confirm, "ask", lambda *a, **k: True)
    assert prompt.confirm("Proceed?") is True


def test_confirm_declined(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt.Confirm, "ask", lambda *a, **k: False)
    assert prompt.confirm("Proceed?") is False
