"""Supabase Admin API client seam for user sync.

The :class:`AdminClient` protocol is the testable boundary — the service layer
talks only to it, so tests inject a fake and never need the ``supabase`` package.
:func:`make_admin_client` builds the real implementation lazily, so the heavy
dependency is imported only when ``jt supabase sync-users`` actually runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from janitor.logging import get_logger
from janitor.models.system import AdminUser

if TYPE_CHECKING:
    from supabase import Client

__all__ = ["AdminClient", "make_admin_client"]

logger = get_logger(__name__)


def _as_str(value: object) -> str | None:
    """Coerce an optional metadata value to ``str`` (or ``None``)."""
    return str(value) if value is not None else None


class AdminClient(Protocol):
    """Minimal auth-admin surface the user-sync logic depends on."""

    def list_users(self) -> list[AdminUser]:
        """Return all auth users visible to this (service-role) client."""
        ...

    def upsert_user(self, user: AdminUser, password: str) -> None:
        """Create (or replace) ``user`` locally with ``password``, preserving id."""
        ...


class _SupabaseAdminClient:  # pragma: no cover - exercised only against a live Supabase
    """Real :class:`AdminClient` backed by ``supabase-py``."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def list_users(self) -> list[AdminUser]:
        response = self._client.auth.admin.list_users()
        raw = response if isinstance(response, list) else getattr(response, "users", [])
        users: list[AdminUser] = []
        for user in raw:
            metadata = dict(getattr(user, "user_metadata", None) or {})
            users.append(
                AdminUser(
                    id=str(user.id),
                    email=getattr(user, "email", "") or "",
                    role=_as_str(metadata.get("role")),
                    user_metadata=metadata,
                )
            )
        return users

    def upsert_user(self, user: AdminUser, password: str) -> None:
        # Replace any existing local user sharing this id/email, then recreate
        # with the prod id preserved so loaded data (keyed on that id) lines up.
        for existing in self.list_users():
            if existing.id == user.id or existing.email == user.email:
                self._client.auth.admin.delete_user(existing.id)
        self._client.auth.admin.create_user(
            {
                "id": user.id,
                "email": user.email,
                "password": password,
                "email_confirm": True,
                "user_metadata": user.user_metadata,
            }
        )


def make_admin_client(url: str, service_key: str) -> AdminClient:
    """Build a real Admin API client. Imports ``supabase`` lazily."""
    try:
        from supabase import create_client
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "The 'supabase' package is required for user sync. "
            "Install it with: uv tool install 'janitor-cli[supabase]'"
        ) from exc
    return _SupabaseAdminClient(create_client(url, service_key))
