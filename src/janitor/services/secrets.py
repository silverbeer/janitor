"""Shared secret resolution via Varlock + 1Password.

janitor owns the convention so SB apps don't copy secrets plumbing per repo:
each repo's ``.env.schema`` ``@import``s the base emitted here and adds only its
own variables. ``jt secrets run`` wraps ``varlock run`` to resolve those values
(from 1Password locally; the cloud path is unchanged and never runs varlock).
"""

from __future__ import annotations

import re
from pathlib import Path

from janitor.config import config_path
from janitor.logging import get_logger
from janitor.models.system import SecretsParityReport
from janitor.services.shell import ShellRunner, which

__all__ = ["ONE_PASSWORD_PLUGIN_VERSION", "SecretsService"]

#: Matches a `NAME=` declaration (env var name at the start of a non-comment line).
_VAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=")
#: Matches a k8s `- name: NAME` immediately wired to a secretKeyRef.
_SECRET_ENV_RE = re.compile(
    r"-\s*name:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\n\s*valueFrom:\s*\n\s*secretKeyRef:",
)

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
#   # @import(~/.config/janitor/base/.env.schema)
#
# 1Password layout (SB convention):
#   - ONE "API Credential" item per app+env, titled "<app>-<env>" (e.g. stk-prod).
#   - Each secret is its own concealed field: service_role_key, anon_key, url,
#     db_url, jwt_secret, ...  (NOT a Secure Note — a blob can't be field-addressed.)
#   - Reference a field: op://<vault>/<app>-<env>/<field>
#     (in 1Password: the field -> down-arrow -> Copy Secret Reference.)
#
# Contract:
#   - env var names match the repo's Helm ExternalSecret keys (local == cloud)
#   - secret -> op() locally + AWS Secrets Manager in cloud; config -> plain value
#
# @currentEnv=$APP_ENV
# @plugin(@varlock/1password-plugin@{ONE_PASSWORD_PLUGIN_VERSION})
# @initOp(allowAppAuth=true, useCliWithServiceAccount=true)
# ---
# Environment flag: dev locally, prod in cloud (k8s sets APP_ENV, which wins by
# process-env precedence).
# @type=enum(dev, prod)
APP_ENV=dev
"""

# NOTE: `useCliWithServiceAccount=true` makes the plugin resolve via the `op`
# CLI (which authenticates through the unlocked desktop app) instead of the WASM
# SDK, whose app-auth path fails with "Unable to authenticate with 1Password"
# even when `op` itself works. Requires the 1Password CLI + desktop integration.


class SecretsService:
    """Locate varlock, manage the shared base schema, and run wrapped commands."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self.runner = runner or ShellRunner()

    def varlock_available(self) -> bool:
        """True when the ``varlock`` CLI is installed."""
        return which("varlock") is not None

    @staticmethod
    def base_schema_path() -> Path:
        """Stable path the shared base schema is written to (repos import this).

        Named ``.env.schema`` in its own dir: varlock only imports files whose
        name starts with ``.env`` and schema-parses ``.env.schema``.
        """
        return config_path().parent / "base" / ".env.schema"

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

    # ---- init ---------------------------------------------------------------

    @staticmethod
    def _repo_schema_template(app: str, base_path: Path) -> str:
        prefix = app.upper().replace("-", "_").replace(".", "_")
        return (
            f"# {app} env schema — managed with `jt secrets`. Commit this file "
            "(references only, no secret values).\n"
            f"# @import({base_path})\n"
            "# ---\n"
            "# Declare this app's variables below. Secrets resolve from 1Password\n"
            "# locally and from the platform (k8s/AWS) in cloud.\n"
            "#\n"
            f"# 1Password: one 'API Credential' item titled '{app}-prod', one field\n"
            "# per secret. Copy each ref via the field's 'Copy Secret Reference'.\n"
            "#\n"
            "# @required @sensitive\n"
            f"# {prefix}_SERVICE_ROLE_KEY=op(op://<vault>/{app}-prod/service_role_key)\n"
            "# @required @sensitive\n"
            f"# {prefix}_DB_URL=op(op://<vault>/{app}-prod/db_url)\n"
            "# @required\n"
            "# LOG_LEVEL=INFO\n"
        )

    def scaffold_schema(self, target_dir: Path, app: str) -> tuple[Path, str]:
        """Create a repo ``.env.schema`` importing the shared base.

        Returns ``(path, status)`` where status is ``created``, ``exists``
        (left untouched), or ``would-create`` (dry-run).
        """
        path = target_dir / ".env.schema"
        if path.exists():
            return path, "exists"
        if self.runner.dry_run:
            return path, "would-create"
        path.write_text(self._repo_schema_template(app, self.base_schema_path()), encoding="utf-8")
        logger.info("secrets.init.scaffold", path=str(path))
        return path, "created"

    def ensure_gitignore_allows(self, target_dir: Path) -> str:
        """Ensure ``.env.schema`` is committable; add a negation if it's ignored.

        Returns ``ok`` (already committable), ``fixed`` (added ``!.env.schema``),
        or ``would-fix`` (dry-run).
        """
        ignored = (
            self.runner.run(
                ["git", "-C", str(target_dir), "check-ignore", ".env.schema"]
            ).returncode
            == 0
        )
        if not ignored:
            return "ok"
        if self.runner.dry_run:
            return "would-fix"
        gitignore = target_dir / ".gitignore"
        with gitignore.open("a", encoding="utf-8") as handle:
            handle.write("\n# Varlock schema is reference-only — safe to commit\n!.env.schema\n")
        logger.info("secrets.init.gitignore", path=str(gitignore))
        return "fixed"

    # ---- doctor -------------------------------------------------------------

    @staticmethod
    def parse_schema_vars(schema_path: Path) -> set[str]:
        """Return the variable names declared in a ``.env.schema`` file."""
        names: set[str] = set()
        for line in schema_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _VAR_RE.match(stripped)
            if match:
                names.add(match.group(1))
        return names

    @staticmethod
    def helm_secret_env_vars(helm_dir: Path) -> set[str]:
        """Return env var names wired to a ``secretKeyRef`` across Helm templates."""
        names: set[str] = set()
        if not helm_dir.is_dir():
            return names
        for path in helm_dir.rglob("*.yaml"):
            names.update(_SECRET_ENV_RE.findall(path.read_text(encoding="utf-8")))
        return names

    def parity(self, schema_vars: set[str], cloud_vars: set[str]) -> SecretsParityReport:
        """Compare schema-declared vars against cloud secret-injected vars."""
        return SecretsParityReport(
            matched=sorted(schema_vars & cloud_vars),
            only_schema=sorted(schema_vars - cloud_vars),
            only_cloud=sorted(cloud_vars - schema_vars),
        )
