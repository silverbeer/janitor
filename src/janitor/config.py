"""Configuration loading for Janitor.

Settings resolve in the following precedence order (highest first):

1. Explicit CLI flags (handled in command modules).
2. Environment variables prefixed with ``JANITOR_``.
3. Values from ``~/.config/janitor/config.toml``.
4. Built-in defaults defined on the models below.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

__all__ = [
    "DiskConfig",
    "DockerConfig",
    "JanitorConfig",
    "LogsConfig",
    "SupabaseConfig",
    "config_path",
    "get_config",
    "load_config",
]


def config_path() -> Path:
    """Return the path to the user config file, honoring ``XDG_CONFIG_HOME``."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "janitor" / "config.toml"


class DockerConfig(BaseModel):
    """Docker cleanup defaults."""

    prune_volumes: bool = Field(
        default=False,
        description="Include unused volumes in aggressive prune operations.",
    )
    prune_build_cache: bool = Field(
        default=True,
        description="Remove build cache during reclaim.",
    )


class DiskConfig(BaseModel):
    """Disk scanning defaults."""

    scan_paths: list[Path] = Field(
        default_factory=lambda: [Path.home()],
        description="Roots scanned for largest files and directories.",
    )
    top_n: int = Field(default=20, ge=1, le=500, description="Entries to report.")
    min_size_mb: int = Field(
        default=100,
        ge=0,
        description="Ignore files/dirs smaller than this when scanning.",
    )


class LogsConfig(BaseModel):
    """Log discovery and rotation defaults."""

    paths: list[Path] = Field(
        default_factory=lambda: [Path("/var/log"), Path.home() / "Library" / "Logs"],
        description="Directories scanned for log files.",
    )
    max_age_days: int = Field(
        default=30,
        ge=0,
        description="Logs older than this are eligible for cleanup.",
    )
    min_size_mb: int = Field(default=10, ge=0, description="Report logs above this size.")


class SupabaseConfig(BaseModel):
    """Supabase project discovery defaults."""

    search_paths: list[Path] = Field(
        default_factory=lambda: [Path.home() / "gitrepos", Path.home() / "projects"],
        description="Directories scanned for Supabase projects.",
    )
    backup_dir: Path = Field(
        default_factory=lambda: Path.home() / ".janitor" / "backups" / "supabase",
        description="Destination for timestamped backups.",
    )


class JanitorConfig(BaseSettings):
    """Top-level Janitor configuration.

    Environment variables use the ``JANITOR_`` prefix with ``__`` as the nested
    delimiter, e.g. ``JANITOR_DISK__TOP_N=50``.
    """

    model_config = SettingsConfigDict(
        env_prefix="JANITOR_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    dry_run: bool = Field(default=False, description="Preview actions without executing.")
    assume_yes: bool = Field(default=False, description="Skip confirmation prompts.")
    log_level: str = Field(default="INFO", description="structlog level: DEBUG..CRITICAL.")
    log_json: bool = Field(default=False, description="Emit logs as JSON instead of console.")

    docker: DockerConfig = Field(default_factory=DockerConfig)
    disk: DiskConfig = Field(default_factory=DiskConfig)
    logs: LogsConfig = Field(default_factory=LogsConfig)
    supabase: SupabaseConfig = Field(default_factory=SupabaseConfig)

    #: TOML values seeded by :func:`load_config`. Class-level so the source
    #: callable below can read them without threading state through ``__init__``.
    _toml_values: dict[str, Any] = {}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Order sources so env wins over the TOML file, which wins over defaults."""

        def toml_source() -> dict[str, Any]:
            return dict(cls._toml_values)

        return (init_settings, env_settings, dotenv_settings, toml_source, file_secret_settings)  # type: ignore[return-value]


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(path: Path | None = None) -> JanitorConfig:
    """Load configuration from disk, environment, and defaults.

    Args:
        path: Optional explicit config path. Defaults to :func:`config_path`.
    """
    resolved = path or config_path()
    # File values feed a dedicated source (lower priority than env) rather than
    # init kwargs, which would otherwise outrank environment variables.
    JanitorConfig._toml_values = _read_toml(resolved)
    try:
        return JanitorConfig()
    finally:
        JanitorConfig._toml_values = {}


@lru_cache(maxsize=1)
def get_config() -> JanitorConfig:
    """Return a cached configuration instance for the current process."""
    return load_config()
