from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["own", "foreign", "missing", "deleted", "wrong_id"])
async def test_c005_invitation_checks_every_selected_project(case):
    from services.construction_invitations import (
        require_construction_invitation_projects,
    )
    from services.construction_permissions import ConstructionScopeDeniedError

    tenant_id = uuid4()
    projects = [uuid4(), uuid4()]
    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=tenant_id, status="active",
    )
    calls = []

    class ProjectRepository:
        async def get_active_project(self, *, tenant_id, project_id):
            calls.append((tenant_id, project_id))
            if project_id == projects[1] and case == "missing":
                return None
            project = SimpleNamespace(
                id=project_id,
                tenant_id=actor.tenant_id,
                deleted_at=None,
            )
            if project_id == projects[1]:
                if case == "foreign":
                    project.tenant_id = uuid4()
                elif case == "deleted":
                    project.deleted_at = object()
                elif case == "wrong_id":
                    project.id = uuid4()
            return project

    arguments = dict(
        actor=actor,
        scopes=[("project", project_id) for project_id in projects],
        project_repository=ProjectRepository(),
    )
    if case == "own":
        assert await require_construction_invitation_projects(**arguments) is None
    else:
        with pytest.raises(ConstructionScopeDeniedError):
            await require_construction_invitation_projects(**arguments)

    assert calls == [(tenant_id, project_id) for project_id in projects]


@pytest.mark.asyncio
async def test_c005_tenant_invitation_does_not_query_projects():
    from services.construction_invitations import (
        require_construction_invitation_projects,
    )

    actor = SimpleNamespace(
        user_id=uuid4(), tenant_id=uuid4(), status="active",
    )
    assert await require_construction_invitation_projects(
        actor=actor,
        scopes=[("tenant", actor.tenant_id)],
        project_repository=None,
    ) is None
