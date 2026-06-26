"""Supabase project discovery, backup, hygiene, and restore service."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from janitor.config import SupabaseConfig
from janitor.logging import get_logger
from janitor.models.system import (
    AdminUser,
    BackupDirReport,
    BackupFile,
    RestoreResult,
    SupabaseProject,
    UserSyncResult,
)
from janitor.services.shell import ShellRunner, which
from janitor.services.supabase_admin import AdminClient

__all__ = ["SupabaseService"]

logger = get_logger(__name__)

_SECONDS_PER_DAY = 86_400
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _split_pg_secret(url: str) -> tuple[str, str | None]:
    """Split a Postgres URL into a password-free URI and the password.

    The sanitized URI is safe to place on the command line / in logs; the
    password is returned separately to pass via ``PGPASSWORD``.
    """
    parts = urlsplit(url)
    user = parts.username or ""
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    userinfo = f"{user}@" if user else ""
    sanitized = urlunsplit(
        (parts.scheme, f"{userinfo}{host}{port}", parts.path, parts.query, parts.fragment)
    )
    return sanitized, parts.password


def _is_local_url(url: str) -> bool:
    """True when ``url`` points at a loopback host (restore safety guard)."""
    return urlsplit(url).hostname in _LOCAL_HOSTS


def _match_like(value: str, pattern: str) -> bool:
    """Case-insensitive SQL-LIKE-ish match supporting a single ``%`` wildcard."""
    value, pattern = value.lower(), pattern.lower()
    if pattern.startswith("%") and pattern.endswith("%"):
        return pattern.strip("%") in value
    if pattern.endswith("%"):
        return value.startswith(pattern[:-1])
    if pattern.startswith("%"):
        return value.endswith(pattern[1:])
    return value == pattern


class SupabaseService:
    """Discover local Supabase projects and create backups."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self.runner = runner or ShellRunner()

    def cli_available(self) -> bool:
        """True when the Supabase CLI is installed."""
        return which("supabase") is not None

    def discover(self, search_paths: list[Path], *, max_depth: int = 3) -> list[SupabaseProject]:
        """Find directories containing a ``supabase/config.toml`` marker."""
        projects: list[SupabaseProject] = []
        seen: set[Path] = set()
        for base in search_paths:
            if not base.is_dir():
                continue
            for marker in self._find_markers(base, max_depth=max_depth):
                project_root = marker.parent.parent
                if project_root in seen:
                    continue
                seen.add(project_root)
                projects.append(
                    SupabaseProject(
                        path=project_root,
                        name=project_root.name,
                        running=False,
                    )
                )
        return projects

    def resolve_projects(
        self, config: SupabaseConfig, name: str | None = None
    ) -> list[SupabaseProject]:
        """Resolve projects by config key (with explicit ``path``) + discovery.

        Configured projects that declare a ``path`` are keyed by their config
        name (e.g. ``stk``) and take precedence over auto-discovered ones (keyed
        by directory name), so a project whose folder differs from its config
        key still resolves. ``name`` filters to a single project.
        """
        merged: dict[str, SupabaseProject] = {p.name: p for p in self.discover(config.search_paths)}
        for key, proj in config.projects.items():
            if proj.path is not None:
                merged[key] = SupabaseProject(path=proj.path.expanduser(), name=key)
        if name is not None:
            return [merged[name]] if name in merged else []
        return list(merged.values())

    @staticmethod
    def _find_markers(base: Path, *, max_depth: int) -> list[Path]:
        markers: list[Path] = []
        base_depth = len(base.parts)
        for path in base.rglob("supabase/config.toml"):
            if len(path.parts) - base_depth <= max_depth + 1:
                markers.append(path)
        return markers

    def status(self, project: SupabaseProject) -> SupabaseProject:
        """Query ``supabase status`` for a project and update its state."""
        if not self.cli_available():
            return project.model_copy(update={"detail": "supabase CLI not installed"})
        result = self.runner.run(
            ["supabase", "status", "--workdir", str(project.path)],
            timeout=30,
        )
        running = result.ok and "API URL" in result.stdout
        return project.model_copy(
            update={
                "running": running,
                "detail": "running" if running else "stopped",
            }
        )

    def backup(self, project: SupabaseProject, backup_dir: Path) -> Path:
        """Create a timestamped DB dump for ``project``.

        Returns:
            The path to the backup file (which may not exist in dry-run mode).
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination = backup_dir / f"{project.name}-{timestamp}.sql"
        self.runner.run(
            ["supabase", "db", "dump", "--workdir", str(project.path), "-f", str(destination)],
            mutating=True,
            timeout=600,
        )
        logger.info("supabase.backup", project=project.name, path=str(destination))
        return destination

    # ---- backup-dir hygiene -------------------------------------------------

    @staticmethod
    def _backup_glob(project_name: str) -> str:
        """Glob matching this project's dumps (``<name>-<timestamp>.sql``)."""
        return f"{project_name}-*.sql"

    def list_backups(self, project_name: str, backup_dir: Path) -> list[BackupFile]:
        """Return this project's dumps in ``backup_dir``, newest first."""
        directory = backup_dir.expanduser()
        if not directory.is_dir():
            return []
        now = datetime.now().timestamp()
        files: list[BackupFile] = []
        for path in directory.glob(self._backup_glob(project_name)):
            if not path.is_file():
                continue
            stat = path.stat()
            files.append(
                BackupFile(
                    path=path,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    age_days=int((now - stat.st_mtime) // _SECONDS_PER_DAY),
                )
            )
        files.sort(key=lambda f: f.mtime, reverse=True)
        return files

    @staticmethod
    def _select_prunable(
        files: list[BackupFile],
        *,
        retention_count: int,
        retention_days: int,
    ) -> list[BackupFile]:
        """Return the files (assumed newest-first) that violate retention.

        A file is prunable if it falls beyond ``retention_count`` OR is older
        than ``retention_days``. Either limit at 0 disables that rule.
        """
        prunable: list[BackupFile] = []
        for index, file in enumerate(files):
            too_many = retention_count > 0 and index >= retention_count
            too_old = retention_days > 0 and file.age_days > retention_days
            if too_many or too_old:
                prunable.append(file)
        return prunable

    def report(
        self,
        project_name: str,
        backup_dir: Path,
        *,
        retention_count: int,
        retention_days: int,
        max_dir_size_mb: int,
    ) -> BackupDirReport:
        """Build a health report for a project's backup directory."""
        files = self.list_backups(project_name, backup_dir)
        prunable = self._select_prunable(
            files, retention_count=retention_count, retention_days=retention_days
        )
        return BackupDirReport(
            project=project_name,
            directory=backup_dir.expanduser(),
            files=files,
            total_size=sum(f.size for f in files),
            max_size=max_dir_size_mb * 1024 * 1024,
            retention_count=retention_count,
            retention_days=retention_days,
            prunable=prunable,
        )

    def prune_backups(
        self,
        project_name: str,
        backup_dir: Path,
        *,
        retention_count: int,
        retention_days: int,
    ) -> list[BackupFile]:
        """Delete backups beyond retention. Honors the runner's dry-run mode.

        Returns:
            The files that were (or, in dry-run, would be) removed.
        """
        files = self.list_backups(project_name, backup_dir)
        prunable = self._select_prunable(
            files, retention_count=retention_count, retention_days=retention_days
        )
        for file in prunable:
            if self.runner.dry_run:
                logger.info("supabase.prune.dry_run", path=str(file.path))
                continue
            try:
                file.path.unlink()
                logger.info("supabase.prune.remove", path=str(file.path))
            except OSError as exc:  # pragma: no cover - filesystem edge
                logger.warning("supabase.prune.failed", path=str(file.path), error=str(exc))
        return prunable

    # ---- restore-from-prod --------------------------------------------------

    def pg_client_available(self) -> bool:
        """True when both ``pg_dump`` and ``psql`` are on PATH."""
        return which("pg_dump") is not None and which("psql") is not None

    def restore_from_prod(
        self,
        project_name: str,
        project_path: Path,
        *,
        local_db_url: str,
        prod_db_url: str,
        data_schemas: list[str],
    ) -> RestoreResult:
        """Reset the local DB to migrations, then load prod data into it.

        Steps: ``supabase db reset`` (schema from migrations) → ``pg_dump`` the
        prod data for ``data_schemas`` → ``psql`` load into local. The prod
        dump is data-only and deleted after load (it may hold PII). Passwords
        travel via ``PGPASSWORD``, never the argument vector.

        Raises:
            ValueError: if ``local_db_url`` does not point at a loopback host.
        """
        if not _is_local_url(local_db_url):
            raise ValueError(
                f"refusing to restore into non-local target: {local_db_url!r} "
                "(local_db_url must point at localhost / 127.0.0.1)"
            )
        dump_path = Path(tempfile.gettempdir()) / f"{project_name}-prod-restore.sql"

        if self.runner.dry_run:
            logger.info("supabase.restore.dry_run", project=project_name)
            return RestoreResult(project=project_name, dump_path=dump_path, dry_run=True)

        # 1. Reset local — schema comes from migrations, not the dump.
        self.runner.run(
            ["supabase", "db", "reset", "--workdir", str(project_path)],
            mutating=True,
            check=True,
            timeout=600,
        )
        # 2. Dump prod data-only for the configured schemas.
        prod_uri, prod_pw = _split_pg_secret(prod_db_url)
        schema_args: list[str] = []
        for schema in data_schemas:
            schema_args += ["--schema", schema]
        self.runner.run(
            [
                "pg_dump",
                "--data-only",
                "--disable-triggers",
                "--no-owner",
                "--no-acl",
                *schema_args,
                "--file",
                str(dump_path),
                prod_uri,
            ],
            check=True,
            timeout=1800,
            env={"PGPASSWORD": prod_pw} if prod_pw else None,
        )
        dumped = dump_path.stat().st_size if dump_path.exists() else 0
        # 3. Load into local, then shred the PII-bearing dump.
        local_uri, local_pw = _split_pg_secret(local_db_url)
        try:
            self.runner.run(
                [
                    "psql",
                    "--single-transaction",
                    "--set",
                    "ON_ERROR_STOP=on",
                    "--dbname",
                    local_uri,
                    "--file",
                    str(dump_path),
                ],
                mutating=True,
                check=True,
                timeout=1800,
                env={"PGPASSWORD": local_pw} if local_pw else None,
            )
        finally:
            dump_path.unlink(missing_ok=True)
        logger.info("supabase.restore.done", project=project_name, dumped_bytes=dumped)
        return RestoreResult(
            project=project_name,
            reset=True,
            dump_path=dump_path,
            dumped_bytes=dumped,
            loaded=True,
        )

    # ---- sync-users (Admin API) ---------------------------------------------

    @staticmethod
    def _select_users(
        users: list[AdminUser],
        *,
        targets: list[str] | None,
        include_all: bool,
        exclude_patterns: list[str],
    ) -> list[AdminUser]:
        """Pick which prod users to sync.

        ``include_all`` takes everything (minus exclusions); otherwise only
        users whose email is in ``targets``. Exclusions always apply.
        """
        target_set = {t.lower() for t in targets} if targets is not None else None
        selected: list[AdminUser] = []
        for user in users:
            email = user.email.lower()
            if any(_match_like(email, pattern) for pattern in exclude_patterns):
                continue
            if include_all or (target_set is not None and email in target_set):
                selected.append(user)
        return selected

    def sync_users(
        self,
        *,
        prod_admin: AdminClient,
        local_admin: AdminClient,
        password_for: Callable[[AdminUser], str],
        targets: list[str] | None = None,
        include_all: bool = False,
        exclude_patterns: list[str] | None = None,
    ) -> list[UserSyncResult]:
        """Sync selected prod auth users into the local project.

        Each selected user is recreated locally with its prod id preserved and a
        password from ``password_for``. Honors the runner's dry-run mode.
        """
        selected = self._select_users(
            prod_admin.list_users(),
            targets=targets,
            include_all=include_all,
            exclude_patterns=exclude_patterns or [],
        )
        dry = self.runner.dry_run
        results: list[UserSyncResult] = []
        for user in selected:
            password = password_for(user)
            if dry:
                results.append(
                    UserSyncResult(
                        email=user.email,
                        user_id=user.id,
                        password=password,
                        action="synced",
                        dry_run=True,
                    )
                )
                continue
            try:
                local_admin.upsert_user(user, password)
                action, detail = "synced", None
            except Exception as exc:  # report any client failure per-user, keep going
                action, detail = "failed", str(exc)
                logger.warning("supabase.sync_users.failed", email=user.email, error=str(exc))
            results.append(
                UserSyncResult(
                    email=user.email,
                    user_id=user.id,
                    password=password,
                    action=action,
                    detail=detail,
                )
            )
        return results
