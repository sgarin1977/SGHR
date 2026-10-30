"""Fresh eligible OWNER evidence under the existing ownership barrier."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, text

from database.models import ConstructionAccessGrant, User, UserRoleMapping
from database.repositories.platform_membership_serialization import ownership_lock_key


@dataclass(frozen=True)
class EffectiveConstructionOwner:
    grant_id: UUID
    tenant_id: UUID
    user_id: UUID
    expires_at: datetime | None


class ConstructionOwnerBoundaryRepository:
    def __init__(self, session):
        self.session = session

    def _transaction(self):
        session = self.session
        transaction = session.get_transaction()
        if (
            session.in_transaction() is not True
            or transaction is None or transaction.is_active is not True
            or session.get_nested_transaction() is not None
            or getattr(getattr(session.get_bind(), "dialect", None), "name", None) != "postgresql"
        ):
            raise ValueError("Owner evidence requires an outer PostgreSQL transaction.")
        return transaction

    async def list_effective_owners(self, *, tenant_id, as_of):
        if (
            not isinstance(tenant_id, UUID)
            or not isinstance(as_of, datetime)
            or as_of.tzinfo is None or as_of.utcoffset() is None
        ):
            raise ValueError("Owner evidence context is invalid.")
        transaction = self._transaction()
        key = ownership_lock_key(tenant_id=tenant_id) & ((1 << 64) - 1)
        with self.session.no_autoflush:
            settings = await self.session.execute(text("""
                SELECT
                  current_setting('transaction_read_only') AS read_only,
                  current_setting('transaction_isolation') AS isolation,
                  EXISTS (
                    SELECT 1 FROM pg_catalog.pg_locks
                    WHERE locktype = 'advisory'
                      AND pid = pg_catalog.pg_backend_pid()
                      AND database = (
                        SELECT oid FROM pg_catalog.pg_database
                        WHERE datname = current_database()
                      )
                      AND classid = CAST(:hi AS oid)
                      AND objid = CAST(:lo AS oid)
                      AND objsubid = 1 AND granted
                  ) AS ownership_locked
            """), {"hi": key >> 32, "lo": key & ((1 << 32) - 1)})
            context = settings.mappings().one()
            if (
                context["read_only"] != "off"
                or context["isolation"] != "read committed"
                or context["ownership_locked"] is not True
                or self._transaction() is not transaction
            ):
                raise ValueError("Serialized ownership evidence is unavailable.")

            membership = (
                select(UserRoleMapping.id)
                .where(
                    UserRoleMapping.user_id == User.id,
                    UserRoleMapping.tenant_id == tenant_id,
                    UserRoleMapping.status == "active",
                )
                .correlate(User)
                .exists()
            )
            grant = ConstructionAccessGrant
            statement = (
                select(
                    grant.id.label("grant_id"),
                    grant.tenant_id,
                    grant.user_id,
                    grant.expires_at,
                )
                .select_from(grant)
                .join(User, User.id == grant.user_id)
                .where(
                    grant.tenant_id == tenant_id,
                    grant.role == "OWNER",
                    grant.scope_type == "tenant",
                    grant.scope_id == tenant_id,
                    grant.status == "active",
                    grant.revoked_at.is_(None),
                    grant.revoked_by_user_id.is_(None),
                    or_(grant.expires_at.is_(None), grant.expires_at > as_of),
                    User.status == "active",
                    or_(User.tenant_id == tenant_id, membership),
                )
                .order_by(grant.id)
                .with_for_update(of=grant)
                .execution_options(autoflush=False)
            )
            result = await self.session.execute(statement)
            rows = tuple(
                EffectiveConstructionOwner(**dict(row))
                for row in result.mappings().all()
            )
            if self._transaction() is not transaction:
                raise ValueError("Owner evidence transaction changed.")
        return rows
