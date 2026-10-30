"""Protect the last effective tenant OWNER during ordinary grant changes."""
from datetime import datetime, timezone
from uuid import UUID

from database.repositories.construction.construction_owner_boundary import (
    ConstructionOwnerBoundaryRepository, EffectiveConstructionOwner,
)
from services.construction.construction_grants import ConstructionGrantOperationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError


class ConstructionLastOwnerChangeDeniedError(ConstructionPermissionDeniedError):
    pass


def _now(provider):
    value = provider()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ConstructionGrantOperationError("Owner boundary server clock is invalid.")
    return value.astimezone(timezone.utc)


async def require_owner_survival_for_grants(
    *, session, tenant_id, grants, now_provider,
):
    if (
        not isinstance(tenant_id, UUID)
        or not isinstance(grants, (list, tuple))
        or not callable(now_provider)
    ):
        raise ConstructionGrantOperationError("Owner boundary context is invalid.")
    affected = any(
        getattr(grant, "role", None) == "OWNER"
        and getattr(grant, "scope_type", None) == "tenant"
        and getattr(grant, "scope_id", None) == tenant_id
        and getattr(grant, "tenant_id", None) == tenant_id
        for grant in grants
    )
    if not affected:
        return
    try:
        rows = await ConstructionOwnerBoundaryRepository(session).list_effective_owners(
            tenant_id=tenant_id, as_of=_now(now_provider),
        )
    except ConstructionGrantOperationError:
        raise
    except Exception:
        raise ConstructionGrantOperationError("Owner evidence is unavailable.") from None
    if not isinstance(rows, tuple):
        raise ConstructionGrantOperationError("Owner evidence is invalid.")
    checked_at = _now(now_provider)
    seen = set()
    surviving_users = set()
    for row in rows:
        if (
            not isinstance(row, EffectiveConstructionOwner)
            or not isinstance(row.grant_id, UUID)
            or not isinstance(row.user_id, UUID)
            or row.tenant_id != tenant_id
            or row.grant_id in seen
            or (
                row.expires_at is not None
                and (
                    not isinstance(row.expires_at, datetime)
                    or row.expires_at.tzinfo is None
                    or row.expires_at.utcoffset() is None
                )
            )
        ):
            raise ConstructionGrantOperationError("Owner evidence is invalid.")
        seen.add(row.grant_id)
        if row.expires_at is None or row.expires_at > checked_at:
            surviving_users.add(row.user_id)
    if not surviving_users:
        raise ConstructionLastOwnerChangeDeniedError(
            "The last tenant OWNER requires a separate ownership operation."
        )
