import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from database.construction import set_construction_tenant_context
from database.construction_session import (
    get_construction_session_factory,
)
from database.repositories.construction import (
    ConstructionAccessRepository,
)
from database.session import async_session
from services.api_identity import ApiActorContext
from services.construction.construction_grants import (
    ConstructionAccessContextResolver,
)
from services.platform_company_profile_access import (
    PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    resolve_company_profile_permissions,
)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_CONSTRUCTION_SMOKE") != "1",
    reason="Set RUN_CONSTRUCTION_SMOKE=1 for Supabase smoke.",
)
async def test_profile_permission_follows_active_grant():
    async with async_session() as platform:
        fixture = (await platform.execute(text("""
            SELECT t.id AS tenant_id, u.id AS user_id
            FROM tenants t
            JOIN users u ON u.tenant_id = t.id
            WHERE t.status = 'active'
              AND u.status = 'active'
              AND NOT EXISTS (
                  SELECT 1 FROM construction_access_grants g
                  WHERE g.tenant_id = t.id
                    AND g.user_id = u.id
                    AND g.role IN ('OWNER', 'ADMIN')
                    AND g.scope_type = 'tenant'
                    AND g.scope_id = t.id
                    AND g.status = 'active'
                    AND (g.expires_at IS NULL OR g.expires_at > now())
              )
            LIMIT 1
        """))).mappings().one_or_none()

    assert fixture is not None, "Need an active user without OWNER/ADMIN grant."
    tenant_id = fixture["tenant_id"]
    user_id = fixture["user_id"]
    now = datetime.now(UTC)
    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    factory = get_construction_session_factory()
    async with factory() as session:
        transaction = await session.begin()
        try:
            await set_construction_tenant_context(
                session, tenant_id=tenant_id
            )
            repository = ConstructionAccessRepository(session)
            grant = await repository.create_grant(
                tenant_id=tenant_id,
                user_id=user_id,
                role="ADMIN",
                scope_type="tenant",
                scope_id=tenant_id,
                expires_at=now + timedelta(minutes=15),
                granted_by_user_id=user_id,
                granted_at=now,
            )

            async def has_permission(as_of):
                context = await ConstructionAccessContextResolver(
                    repository=repository,
                    now_provider=lambda: as_of,
                ).resolve(actor=actor)
                effective = resolve_company_profile_permissions(
                    actor=actor,
                    construction_access_context=context,
                )
                return PLATFORM_ORGANIZATION_PROFILE_MANAGE in effective

            assert await has_permission(now)
            assert not await has_permission(now + timedelta(minutes=16))

            await repository.revoke_grant(
                grant=grant,
                revoked_by_user_id=user_id,
                revoked_at=now + timedelta(minutes=1),
            )
            assert not await has_permission(now + timedelta(minutes=1))
        finally:
            if transaction.is_active:
                await transaction.rollback()
