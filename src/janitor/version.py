"""Single source of truth for the package version."""

from __future__ import annotations

from importlib import metadata

__all__ = ["__version__", "get_version"]


def get_version() -> str:
    """Return the installed package version, falling back to a dev marker."""
    try:
        return metadata.version("janitor-cli")
    except metadata.PackageNotFoundError:  # pragma: no cover - source checkout
        return "0.1.0.dev0"


__version__ = get_version()
