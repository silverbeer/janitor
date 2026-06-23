"""Tests for the Homebrew service."""

from __future__ import annotations

import json

from janitor.services.brew import BrewService
from tests.conftest import FakeRunner


def test_unavailable(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.brew.which", lambda _: None)
    status = BrewService(runner=fake_runner).status()
    assert status.available is False
    assert status.outdated == []


def test_status_with_outdated(fake_runner: FakeRunner, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("janitor.services.brew.which", lambda _: "/opt/homebrew/bin/brew")
    fake_runner.stub(["brew", "--prefix"], stdout="/opt/homebrew\n")
    payload = {
        "formulae": [
            {
                "name": "ansible",
                "installed_versions": ["13.1.0"],
                "current_version": "14.0.0",
            }
        ],
        "casks": [
            {
                "name": "docker",
                "installed_versions": ["4.0"],
                "current_version": "4.1",
            }
        ],
    }
    fake_runner.stub(["brew", "outdated"], stdout=json.dumps(payload))
    status = BrewService(runner=fake_runner).status()
    assert status.available is True
    assert status.prefix == "/opt/homebrew"
    assert status.outdated_count == 2
    assert status.outdated[1].is_cask is True


def test_upgrade(fake_runner: FakeRunner) -> None:
    fake_runner.stub(["brew", "upgrade"], stdout="upgraded")
    assert BrewService(runner=fake_runner).upgrade() == "upgraded"


def test_cleanup(fake_runner: FakeRunner) -> None:
    fake_runner.stub(["brew", "cleanup"], stdout="cleaned")
    assert BrewService(runner=fake_runner).cleanup() == "cleaned"
