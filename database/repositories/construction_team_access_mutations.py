from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, update

from database.models import ConstructionAccessGrant
from database.repositories.construction_access_mutations import ConstructionAccessMutationRepository


class ConstructionTeamAccessMutationRepository(ConstructionAccessMutationRepository):
    """Tenant-local revocation; the caller owns permissions, audit and transaction."""

    async def revoke_effective_user_grants(
        self, *, tenant_id, user_id, revoked_by_user_id, as_of,
    ):
        if (
            not all(isinstance(value, UUID) for value in (tenant_id, user_id, revoked_by_user_id))
            or not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None
            or not callable(getattr(self.session, "in_transaction", None))
            or not self.session.in_transaction()
        ):
            raise ValueError("Team revocation requires UUID context, aware server time and an active transaction.")
        instant = as_of.astimezone(timezone.utc)
        grant = ConstructionAccessGrant
        statement = (
            update(grant)
            .where(
                grant.tenant_id == tenant_id,
                grant.user_id == user_id,
                grant.status == "active",
                grant.revoked_at.is_(None),
                grant.revoked_by_user_id.is_(None),
                or_(grant.expires_at.is_(None), grant.expires_at > instant),
            )
            .values(status="revoked", revoked_by_user_id=revoked_by_user_id, revoked_at=instant)
            .returning(grant)
            .execution_options(
                synchronize_session=False, populate_existing=True, autoflush=False,
            )
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
