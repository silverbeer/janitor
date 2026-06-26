"""Shared secret resolution via Varlock + 1Password.

janitor owns the convention so SB apps don't copy secrets plumbing per repo:
each repo's ``.env.schema`` ``@import``s the base emitted here and adds only its
own variables. ``jt secrets run`` wraps ``varlock run`` to resolve those values
(from 1Password locally; the cloud path is unchanged and never runs varlock).
"""

from __future__ import annotations

from pathlib import Path

from janitor.config import config_path
from janitor.logging import get_logger
from janitor.services.shell import ShellRunner, which

__all__ = ["ONE_PASSWORD_PLUGIN_VERSION", "SecretsService"]

logger = get_logger(__name__)

#: Pinned so every repo resolves with the same plugin (varlock requires a fixed
#: version in @plugin; bumping here updates all consumers at once).
ONE_PASSWORD_PLUGIN_VERSION = "2.0.0"

#: Base schema each repo imports. Holds only shared boilerplate + conventions —
#: never any variables or secret references (those live per-repo).
BASE_SCHEMA = f"""\
# Shared SB base env schema — managed by `jt secrets base`. DO NOT edit by hand.
#
# Each repo's .env.schema imports this and adds only its own variables:
#   # @import(~/.config/janitor/varlock-base.env.schema)
#
# Convention:
#   - 1Password ref:  op://<vault>/<app>-<env>/<field>
#   - env var names match the repo's Helm ExternalSecret keys (one contract)
#   - secret -> op() locally + AWS Secrets Manager in cloud; config -> plain value
#
# @currentEnv=$APP_ENV
# @plugin(@varlock/1password-plugin@{ONE_PASSWORD_PLUGIN_VERSION})
# @initOp(allowAppAuth=true)
# ---
"""


class SecretsService:
    """Locate varlock, manage the shared base schema, and run wrapped commands."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self.runner = runner or ShellRunner()

    def varlock_available(self) -> bool:
        """True when the ``varlock`` CLI is installed."""
        return which("varlock") is not None

    @staticmethod
    def base_schema_path() -> Path:
        """Stable path the shared base schema is written to (repos import this)."""
        return config_path().parent / "varlock-base.env.schema"

    def write_base_schema(self) -> Path:
        """Write the shared base schema. Honors dry-run; returns its path."""
        path = self.base_schema_path()
        if self.runner.dry_run:
            logger.info("secrets.base.dry_run", path=str(path))
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BASE_SCHEMA, encoding="utf-8")
        logger.info("secrets.base.write", path=str(path))
        return path

    def run(self, command: list[str]) -> int:
        """Run ``command`` under ``varlock run`` (interactive). Returns exit code."""
        return self.runner.exec_passthrough(["varlock", "run", "--", *command])
