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
    "SupabaseProjectConfig",
    "config_path",
    "get_config",
    "load_config",
]

#: Default role -> password map for local user sync (DEV credentials only).
_DEFAULT_ROLE_PASSWORDS = {
    "admin": "admin123",
    "default": "fan123",
}


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


class SupabaseProjectConfig(BaseModel):
    """Per-project Supabase backup/restore overrides.

    Unset (``None``) numeric fields fall back to the shared
    :class:`SupabaseConfig` defaults, so a project only declares what differs.
    """

    backup_dir: Path | None = Field(
        default=None,
        description="Override the shared backup dir for this project.",
    )
    retention_count: int | None = Field(
        default=None,
        ge=0,
        description="Keep this many newest backups (0 = unlimited).",
    )
    retention_days: int | None = Field(
        default=None,
        ge=0,
        description="Delete backups older than this many days (0 = no age limit).",
    )
    max_dir_size_mb: int | None = Field(
        default=None,
        ge=0,
        description="Warn when the backup dir exceeds this size (0 = no limit).",
    )

    # ---- restore-from-prod ----
    path: Path | None = Field(
        default=None,
        description="Project root (holds supabase/). Defaults to the discovered path.",
    )
    local_db_url: str = Field(
        default="postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        description="Local Supabase Postgres connection string.",
    )
    prod_db_url_env: str | None = Field(
        default=None,
        description="NAME of the env var holding the prod DB URL (value never stored here).",
    )
    data_schemas: list[str] = Field(
        default_factory=lambda: ["public"],
        description="Schemas whose data is dumped from prod and loaded locally.",
    )
    exclude_user_patterns: list[str] = Field(
        default_factory=list,
        description="SQL LIKE patterns of usernames to skip during user sync.",
    )

    # ---- sync-users (Admin API) ----
    prod_api_url: str | None = Field(
        default=None,
        description="Prod Supabase API URL, e.g. https://<ref>.supabase.co.",
    )
    prod_service_key_env: str | None = Field(
        default=None,
        description="NAME of the env var holding the prod service-role key.",
    )
    local_api_url: str = Field(
        default="http://127.0.0.1:54321",
        description="Local Supabase API URL.",
    )
    local_service_key_env: str | None = Field(
        default=None,
        description="NAME of the env var holding the local service-role key.",
    )
    user_passwords: dict[str, str] = Field(
        default_factory=dict,
        description="email -> local password. The keys are the default sync target list.",
    )


class SupabaseConfig(BaseModel):
    """Supabase project discovery, backup, and retention defaults."""

    search_paths: list[Path] = Field(
        default_factory=lambda: [Path.home() / "gitrepos", Path.home() / "projects"],
        description="Directories scanned for Supabase projects.",
    )
    backup_dir: Path = Field(
        default_factory=lambda: Path.home() / ".janitor" / "backups" / "supabase",
        description="Destination for timestamped backups.",
    )
    retention_count: int = Field(
        default=5,
        ge=0,
        description="Default backups to keep per project (0 = unlimited).",
    )
    retention_days: int = Field(
        default=0,
        ge=0,
        description="Default age limit in days for backups (0 = no age limit).",
    )
    max_dir_size_mb: int = Field(
        default=2000,
        ge=0,
        description="Default size ceiling per backup dir before warning (0 = no limit).",
    )
    role_passwords: dict[str, str] = Field(
        default_factory=lambda: dict(_DEFAULT_ROLE_PASSWORDS),
        description="Role -> password map for local user sync (DEV credentials only).",
    )
    projects: dict[str, SupabaseProjectConfig] = Field(
        default_factory=dict,
        description="Per-project overrides keyed by project name.",
    )

    def project(self, name: str) -> SupabaseProjectConfig:
        """Return the override block for ``name`` (empty defaults if absent)."""
        return self.projects.get(name, SupabaseProjectConfig())

    def resolved_backup_dir(self, name: str) -> Path:
        """Backup dir for ``name`` — per-project override else the shared dir."""
        override = self.project(name).backup_dir
        return (override or self.backup_dir).expanduser()

    def resolved_retention(self, name: str) -> tuple[int, int, int]:
        """Return ``(retention_count, retention_days, max_dir_size_mb)`` for ``name``.

        Per-project values win; ``None`` falls back to the shared defaults.
        """
        proj = self.project(name)
        count = self.retention_count if proj.retention_count is None else proj.retention_count
        days = self.retention_days if proj.retention_days is None else proj.retention_days
        max_mb = self.max_dir_size_mb if proj.max_dir_size_mb is None else proj.max_dir_size_mb
        return count, days, max_mb

    def resolved_prod_db_url(self, name: str) -> str | None:
        """Return the prod DB URL for ``name`` from its configured env var.

        Returns ``None`` when the project declares no ``prod_db_url_env`` or the
        named variable is unset in the environment.
        """
        env_name = self.project(name).prod_db_url_env
        if not env_name:
            return None
        return os.environ.get(env_name) or None

    def resolved_service_key(self, name: str, *, prod: bool) -> str | None:
        """Return the prod or local service-role key for ``name`` from its env var."""
        proj = self.project(name)
        env_name = proj.prod_service_key_env if prod else proj.local_service_key_env
        if not env_name:
            return None
        return os.environ.get(env_name) or None

    def sync_targets(self, name: str) -> list[str]:
        """Default sync list = the emails named in the project's user_passwords."""
        return list(self.project(name).user_passwords.keys())

    def resolved_password(self, name: str, *, email: str, role: str | None) -> str:
        """Resolve a user's local password.

        Order: per-user (``user_passwords[email]``) → role map
        (``role_passwords[role]``) → ``role_passwords['default']`` → ``"fan123"``.
        """
        proj = self.project(name)
        if email in proj.user_passwords:
            return proj.user_passwords[email]
        if role and role in self.role_passwords:
            return self.role_passwords[role]
        return self.role_passwords.get("default", "fan123")


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
