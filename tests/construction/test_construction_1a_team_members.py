from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.construction.construction_permissions import (
    ConstructionAccessContext,
    ConstructionGrantContext,
)
from services.construction.construction_team import ConstructionTeamService


@pytest.mark.asyncio
async def test_c004_team_groups_grants_without_losing_role_scope():
    tenant_id, manager_id, first_user, second_user = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    project_id = uuid4()
    actor = SimpleNamespace(
        user_id=manager_id, tenant_id=tenant_id, status="active"
    )
    context = ConstructionAccessContext(
        user_id=manager_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=("construction.team.manage",),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id
        ),),
    )
    first_grant = SimpleNamespace(
        id=uuid4(), user_id=first_user, role="ADMIN",
        scope_type="tenant", scope_id=tenant_id,
    )
    second_grant = SimpleNamespace(
        id=uuid4(), user_id=first_user, role="OBSERVER",
        scope_type="project", scope_id=project_id,
    )
    third_grant = SimpleNamespace(
        id=uuid4(), user_id=second_user, role="SURVEYOR",
        scope_type="project", scope_id=project_id,
    )
    calls = []

    class Repository:
        async def list_active_grants(self, **kwargs):
            calls.append(kwargs)
            return [first_grant, third_grant, second_grant]

    service = ConstructionTeamService(repository=Repository())
    members = await service.list_team_members(
        actor=actor, access_context=context, project_id=project_id
    )

    assert len(members) == 2
    by_user = {member["user_id"]: member for member in members}
    assert set(by_user) == {first_user, second_user}
    assert set(by_user[first_user]["roles"]) == {"ADMIN", "OBSERVER"}
    assert set(by_user[second_user]["roles"]) == {"SURVEYOR"}
    assert by_user[first_user]["grants"] == [first_grant, second_grant]
    assert by_user[second_user]["grants"] == [third_grant]
    assert len(calls) == 1
    assert calls[0]["tenant_id"] == tenant_id
    assert calls[0]["project_id"] == project_id
