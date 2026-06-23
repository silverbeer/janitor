"""Supabase project discovery and backup service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from janitor.logging import get_logger
from janitor.models.system import SupabaseProject
from janitor.services.shell import ShellRunner, which

__all__ = ["SupabaseService"]

logger = get_logger(__name__)


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
