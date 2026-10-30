"""Canonical storage for the authorized Platform organization bootstrap.

No commit, entitlement decision, role-template change or schema mutation.
This path must use a Platform session, never a browser/Construction connection.
"""
from uuid import UUID

from sqlalchemy import select, text

from database.models import (
    ApiIdempotencyRecord, Language, Tenant, User, UserRoleMapping,
)
from database.repositories.construction import ConstructionAccessRepository


OPERATION = "platform.organization.bootstrap.construction"


class PlatformOrganizationBootstrapRepository:
    def __init__(self, session):
        self.session = session

    async def require_runtime(self):
        if (
            self.session.in_transaction() is not True
            or self.session.get_nested_transaction() is not None
            or self.session.get_bind().dialect.name != "postgresql"
        ):
            raise RuntimeError("Bootstrap requires an outer PostgreSQL transaction.")
        result = await self.session.execute(text(
            "SELECT current_setting('transaction_read_only') AS read_only, "
            "current_setting('transaction_isolation') AS isolation"
        ))
        settings = result.mappings().one()
        if settings["read_only"] != "off" or settings["isolation"] != "read committed":
            raise RuntimeError("Bootstrap requires read-write READ COMMITTED.")

    async def lock_creator(self, *, user_id):
        if not isinstance(user_id, UUID):
            raise ValueError("A canonical creator UUID is required.")
        # All bootstrap requests of this creator serialize here. Under READ
        # COMMITTED the receipt query runs with a fresh snapshot after waiting.
        result = await self.session.execute(
            select(User.id).where(
                User.id == user_id, User.status == "active",
            ).with_for_update()
        )
        return result.scalar_one_or_none()

    async def has_active_creation_identity(self, *, user_id):
        if not isinstance(user_id, UUID):
            raise ValueError("A canonical creator UUID is required.")
        from sqlalchemy import func
        from database.models import UserAccount

        # Authentication and proof verification belong to Platform auth.
        # Here we recheck the current canonical user/account lifecycle.
        # Account presence alone is never accepted as authentication proof.
        account = (
            select(UserAccount.id)
            .where(
                UserAccount.user_id == User.id,
                UserAccount.status == "active",
                func.length(func.trim(UserAccount.platform_user_id)) > 0,
            )
            .correlate(User)
            .exists()
        )
        result = await self.session.execute(
            select(User.id).where(
                User.id == user_id, User.status == "active", account,
            ).execution_options(autoflush=False)
        )
        actual = result.scalar_one_or_none()
        if actual is None:
            return False
        if not isinstance(actual, UUID) or actual != user_id:
            raise RuntimeError("Creator identity projection differs.")
        return True

    async def list_creation_languages(self):
        result = await self.session.execute(
            select(Language.code).where(
                Language.is_active.is_(True),
            ).order_by(Language.code).execution_options(autoflush=False)
        )
        return list(result.scalars().all())

    async def find_receipt(self, *, user_id, key_hash, as_of):
        # Deliberately Platform-only: a new request has no tenant context yet.
        # Identity and this exact bootstrap operation constrain the lookup.
        result = await self.session.execute(
            select(ApiIdempotencyRecord).where(
                ApiIdempotencyRecord.principal_type == "user",
                ApiIdempotencyRecord.principal_id == user_id,
                ApiIdempotencyRecord.operation == OPERATION,
                ApiIdempotencyRecord.key_hash == key_hash,
                ApiIdempotencyRecord.expires_at > as_of,
            ).order_by(ApiIdempotencyRecord.id).limit(2)
            .execution_options(populate_existing=True).with_for_update()
        )
        rows = list(result.scalars().all())
        if len(rows) > 1:
            raise RuntimeError("Bootstrap receipt scope is ambiguous.")
        return rows[0] if rows else None

    async def require_language(self, *, language):
        result = await self.session.execute(
            select(Language.code).where(
                Language.code == language, Language.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none() == language

    async def create_tenant(self, *, tenant_id, name, language, currency, as_of):
        row = Tenant(
            id=tenant_id, name=name, slug="org-" + tenant_id.hex,
            default_language=language, default_currency=currency,
            status="active", extra_metadata={},
            created_at=as_of, updated_at=as_of,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def ensure_creator_membership(self, *, tenant_id, user_id, as_of):
        result = await self.session.execute(
            select(UserRoleMapping.id).where(
                UserRoleMapping.tenant_id == tenant_id,
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.status == "active",
            ).order_by(UserRoleMapping.id).limit(1).with_for_update()
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        access = ConstructionAccessRepository(self.session)
        if await access.has_active_platform_membership(
            tenant_id=tenant_id, user_id=user_id,
        ):
            return None
        row = UserRoleMapping(
            user_id=user_id, tenant_id=tenant_id, role="client",
            status="active", granted_by=user_id,
            granted_at=as_of.replace(tzinfo=None),
        )
        self.session.add(row)
        await self.session.flush()
        return row.id
