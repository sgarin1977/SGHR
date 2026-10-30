from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError,
)
from services.construction.construction_team import ConstructionTeamService


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["own", "missing", "ambiguous", "foreign"])
async def test_c004_team_loads_identity_only_for_visible_members(mode):
    tenant_id, manager_id, member_id = uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(
        user_id=manager_id, tenant_id=tenant_id, status="active",
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
            id=uuid4(), user_id=member_id, role=role,
            scope_type="project", scope_id=uuid4(),
        )
        for role in ("SURVEYOR", "ESTIMATOR")
    ]
    identity_calls = []

    class Repository:
        async def list_active_grants(self, **kwargs):
            return grants

    class IdentityReader:
        async def list_team_identity_accounts(self, **kwargs):
            identity_calls.append(kwargs)
            if mode == "missing":
                return []
            rows = [{
                "user_id": uuid4() if mode == "foreign" else member_id,
                "display_name": "Member",
                "verified_email": "one@example.com",
            }]
            if mode == "ambiguous":
                rows.append({
                    "user_id": member_id, "display_name": "Other",
                    "verified_email": "two@example.com",
                })
            return rows

    service = ConstructionTeamService(
        repository=Repository(), identity_reader=IdentityReader(),
    )
    if mode == "foreign":
        with pytest.raises(ConstructionPermissionDeniedError):
            await service.list_team_members(
                actor=actor, access_context=context,
            )
    else:
        members = await service.list_team_members(
            actor=actor, access_context=context,
        )
        assert len(members) == 1
        member = members[0]
        assert member["user_id"] == member_id
        assert member["grants"] == grants
        assert member["display_name"] == ("Member" if mode == "own" else None)
        assert member["email"] == ("one@example.com" if mode == "own" else None)

    assert identity_calls == [{
        "tenant_id": tenant_id, "user_ids": (member_id,),
    }]
