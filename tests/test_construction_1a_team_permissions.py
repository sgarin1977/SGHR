from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionAccessContext,
    ConstructionGrantContext,
    ConstructionPermissionDeniedError,
)
from services.construction_team import ConstructionTeamService


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown_role", [False, True])
async def test_c004_team_permissions_remain_bound_to_grant_scope(
    unknown_role,
):
    tenant_id, manager_id, member_id = uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(
        user_id=manager_id, tenant_id=tenant_id, status="active"
    )
    context = ConstructionAccessContext(
        user_id=manager_id, tenant_id=tenant_id,
        roles=("OWNER",), permissions=("construction.team.manage",),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id,
        ),),
    )
    grants = [
        SimpleNamespace(
            id=uuid4(), user_id=member_id, role="SURVEYOR",
            scope_type="project", scope_id=uuid4(),
        ),
        SimpleNamespace(
            id=uuid4(), user_id=member_id,
            role="UNKNOWN" if unknown_role else "ESTIMATOR",
            scope_type="project", scope_id=uuid4(),
        ),
    ]
    originals = [vars(grant).copy() for grant in grants]

    class Repository:
        async def list_active_grants(self, **kwargs):
            return grants

    service = ConstructionTeamService(repository=Repository())
    if unknown_role:
        with pytest.raises(ConstructionPermissionDeniedError):
            await service.list_team_members(
                actor=actor, access_context=context,
            )
    else:
        members = await service.list_team_members(
            actor=actor, access_context=context,
        )
        assert len(members) == 1
        entries = members[0]["scoped_permissions"]
        assert len(entries) == 2
        by_grant = {entry["grant_id"]: entry for entry in entries}
        for grant in grants:
            entry = by_grant[grant.id]
            assert entry["role"] == grant.role
            assert entry["scope_type"] == grant.scope_type
            assert entry["scope_id"] == grant.scope_id
            assert entry["permissions"] == sorted(
                CONSTRUCTION_ROLE_PERMISSIONS[grant.role]
            )
        assert "construction.estimates.create" not in (
            by_grant[grants[0].id]["permissions"]
        )
    assert [vars(grant) for grant in grants] == originals
