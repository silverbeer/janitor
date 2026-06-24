"""Supabase project discovery, backup, and backup-dir hygiene service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from janitor.logging import get_logger
from janitor.models.system import BackupDirReport, BackupFile, SupabaseProject
from janitor.services.shell import ShellRunner, which

__all__ = ["SupabaseService"]

logger = get_logger(__name__)

_SECONDS_PER_DAY = 86_400


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
