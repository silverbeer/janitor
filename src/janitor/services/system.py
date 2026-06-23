"""System inspection helpers used by the doctor command."""

from __future__ import annotations

import platform
import sys

from janitor.models.common import HealthStatus, ToolCheck
from janitor.services.shell import ShellRunner, which

__all__ = ["SystemService"]


class SystemService:
    """Probe for the availability and versions of developer tooling."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self.runner = runner or ShellRunner()

    def python_check(self) -> ToolCheck:
        """Report the running Python interpreter version."""
        version = platform.python_version()
        ok = sys.version_info >= (3, 14)
        return ToolCheck(
            name="Python",
            available=True,
            version=version,
            status=HealthStatus.OK if ok else HealthStatus.WARN,
            detail=None if ok else "Janitor targets Python 3.14+",
        )

    def _versioned_tool(
        self,
        name: str,
        executable: str,
        version_args: list[str],
        *,
        required: bool = False,
    ) -> ToolCheck:
        path = which(executable)
        if path is None:
            return ToolCheck(
                name=name,
                available=False,
                status=HealthStatus.ERROR if required else HealthStatus.WARN,
                detail=f"{executable} not found on PATH",
            )
        version = self.runner.capture([executable, *version_args]).splitlines()
        return ToolCheck(
            name=name,
            available=True,
            version=version[0] if version else None,
            status=HealthStatus.OK,
            detail=path,
        )

    def uv_check(self) -> ToolCheck:
        """Report uv availability."""
        return self._versioned_tool("uv", "uv", ["--version"])

    def docker_check(self) -> ToolCheck:
        """Report Docker CLI availability and daemon reachability."""
        path = which("docker")
        if path is None:
            return ToolCheck(
                name="Docker",
                available=False,
                status=HealthStatus.WARN,
                detail="docker not found on PATH",
            )
        info = self.runner.run(["docker", "info", "--format", "{{.ServerVersion}}"])
        if not info.ok:
            return ToolCheck(
                name="Docker",
                available=True,
                status=HealthStatus.WARN,
                detail="CLI present but daemon unreachable",
            )
        return ToolCheck(
            name="Docker",
            available=True,
            version=info.stdout.strip(),
            status=HealthStatus.OK,
            detail="daemon reachable",
        )

    def brew_check(self) -> ToolCheck:
        """Report Homebrew availability."""
        return self._versioned_tool("Homebrew", "brew", ["--version"])

    def kubernetes_check(self) -> ToolCheck:
        """Report kubectl availability and cluster reachability."""
        path = which("kubectl")
        if path is None:
            return ToolCheck(
                name="Kubernetes",
                available=False,
                status=HealthStatus.WARN,
                detail="kubectl not found on PATH",
            )
        ctx = self.runner.run(["kubectl", "config", "current-context"])
        if not ctx.ok:
            return ToolCheck(
                name="Kubernetes",
                available=True,
                status=HealthStatus.WARN,
                detail="kubectl present but no active context",
            )
        return ToolCheck(
            name="Kubernetes",
            available=True,
            version=ctx.stdout.strip(),
            status=HealthStatus.OK,
            detail="context active",
        )

    def supabase_check(self) -> ToolCheck:
        """Report Supabase CLI availability."""
        return self._versioned_tool("Supabase CLI", "supabase", ["--version"])

    def all_checks(self) -> list[ToolCheck]:
        """Run every probe and return the results in display order."""
        return [
            self.python_check(),
            self.uv_check(),
            self.docker_check(),
            self.brew_check(),
            self.kubernetes_check(),
            self.supabase_check(),
        ]
