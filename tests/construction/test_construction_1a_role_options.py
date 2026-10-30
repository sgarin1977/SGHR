from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.dialects import postgresql

import api.routes.construction_role_options as routes
from api.auth import get_current_actor
from database.repositories.construction import ConstructionProjectRepository
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from services.construction.construction_role_options import ConstructionRoleOptionsService
from services.module_access import ModuleDisabledError
from test_construction_1a_invitation_acceptance_http import http_case
from test_construction_1a_invitation_management_http import management_case

@pytest.fixture
def role_case(management_case):
    case = management_case
    case.project_id = uuid4()
    case.project = SimpleNamespace(id=case.project_id, tenant_id=case.actor.tenant_id, deleted_at=None)
    case.statements = []
    async def execute(statement):
        case.statements.append(statement)
        return SimpleNamespace(scalar_one_or_none=lambda: case.project)
    case.session = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    for name in ("commit", "rollback", "flush", "add"):
        setattr(case.session, name, AsyncMock(side_effect=AssertionError("Role options must not write.")))
    case.service = ConstructionRoleOptionsService(
        session=case.session, project_repository=ConstructionProjectRepository(case.session),
    )
    case.app.dependency_overrides[routes.get_role_options_service] = lambda: case.service
    return case

def no_writes(case):
    for name in ("commit", "rollback", "flush", "add"):
        getattr(case.session, name).assert_not_called()

def admin_context(case, *, project=None, owner_elsewhere=False):
    grants = [ConstructionGrantContext("ADMIN", "project" if project else "tenant", project or case.actor.tenant_id)]
    if owner_elsewhere:
        grants.append(ConstructionGrantContext("OWNER", "project", uuid4()))
    roles = ("ADMIN", "OWNER") if owner_elsewhere else ("ADMIN",)
    permissions = CONSTRUCTION_ROLE_PERMISSIONS["OWNER" if owner_elsewhere else "ADMIN"]
    return ConstructionAccessContext(
        user_id=case.actor.user_id, tenant_id=case.actor.tenant_id,
        roles=roles, permissions=tuple(permissions), grants=tuple(grants),
    )

async def get(case, *, scope_type="tenant", scope_id=None, anonymous=False, extra=""):
    if not anonymous:
        case.app.dependency_overrides[get_current_actor] = lambda: case.actor
    target = scope_id if scope_id is not None else case.actor.tenant_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get(
            "/api/v1/construction/team/role-options?scope_type=" + scope_type + "&scope_id=" + str(target) + extra,
            headers={"X-Request-ID": "role-options-http"},
        )

@pytest.mark.asyncio
async def test_owner_tenant_options_use_fixed_policy_without_sql_or_mutations(role_case):
    case = role_case
    response = await get(case, extra="&permissions=untrusted&tenant_id=" + str(uuid4()))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["scope_id"] == str(case.actor.tenant_id) and data["permissions_read_only"] is True
    assert {item["role"] for item in data["roles"]} == {"OWNER", "ADMIN"}
    for item in data["roles"]:
        assert item["permissions"] == sorted(CONSTRUCTION_ROLE_PERMISSIONS[item["role"]])
    assert len(next(item for item in data["roles"] if item["role"] == "OWNER")["permissions"]) == 48
    assert len(next(item for item in data["roles"] if item["role"] == "ADMIN")["permissions"]) == 47
    assert case.statements == []
    no_writes(case)

@pytest.mark.asyncio
async def test_admin_tenant_cannot_offer_owner_role(role_case):
    case = role_case
    case.access = admin_context(case)
    response = await get(case)
    assert response.status_code == 200, response.text
    assert [item["role"] for item in response.json()["data"]["roles"]] == ["ADMIN"]
    no_writes(case)

@pytest.mark.asyncio
async def test_owner_project_options_verify_canonical_project_once(role_case):
    case = role_case
    response = await get(case, scope_type="project", scope_id=case.project_id)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["roles"]) == 7
    assert len(case.statements) == 1
    statement = case.statements[0]
    params = statement.compile().params.values()
    assert case.actor.tenant_id in params and case.project_id in params
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "deleted_at IS NULL" in sql and "FOR UPDATE" not in sql
    no_writes(case)

@pytest.mark.asyncio
async def test_owner_in_other_project_cannot_expand_target_project_role_options(role_case):
    case = role_case
    case.access = admin_context(case, project=case.project_id, owner_elsewhere=True)
    response = await get(case, scope_type="project", scope_id=case.project_id)
    assert response.status_code == 200, response.text
    roles = {item["role"] for item in response.json()["data"]["roles"]}
    assert "ADMIN" in roles and "OWNER" not in roles
    assert len(roles) == 6
    no_writes(case)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    ("anonymous", 401), ("inactive", 403), ("team_other_project", 403), ("foreign_context", 403),
    ("foreign_tenant", 403), ("other_project", 403), ("document", 403), ("module", 403),
    ("invalid_scope", 422), ("invalid_id", 422),
])
async def test_role_options_denies_before_project_query(role_case, failure, status):
    case = role_case
    kind, target = "tenant", case.actor.tenant_id
    if failure == "inactive":
        case.actor.status = "suspended"
    elif failure == "team_other_project":
        case.access = ConstructionAccessContext(
            user_id=case.actor.user_id, tenant_id=case.actor.tenant_id, roles=("PROJECT_MANAGER",),
            permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
            grants=(ConstructionGrantContext("PROJECT_MANAGER", "project", case.project_id),),
        )
        kind, target = "project", uuid4()
    elif failure == "foreign_context":
        case.access = ConstructionAccessContext(
            user_id=case.actor.user_id, tenant_id=uuid4(), roles=case.access.roles,
            permissions=case.access.permissions, grants=case.access.grants,
        )
    elif failure == "foreign_tenant":
        target = uuid4()
    elif failure == "other_project":
        case.access = admin_context(case, project=case.project_id)
        kind, target = "project", uuid4()
    elif failure == "document":
        kind, target = "document", uuid4()
    elif failure == "module":
        case.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "invalid_scope":
        kind = "unregistered"
    elif failure == "invalid_id":
        target = "invalid"
    response = await get(case, scope_type=kind, scope_id=target, anonymous=failure == "anonymous")
    assert response.status_code == status, response.text
    assert case.statements == []
    no_writes(case)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "foreign", "deleted", "id"])
async def test_role_options_rejects_unavailable_project(role_case, failure):
    case = role_case
    if failure == "missing":
        case.project = None
    elif failure == "foreign":
        case.project.tenant_id = uuid4()
    elif failure == "deleted":
        case.project.deleted_at = object()
    else:
        case.project.id = uuid4()
    response = await get(case, scope_type="project", scope_id=case.project_id)
    assert response.status_code == 403, response.text
    assert len(case.statements) == 1
    no_writes(case)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["private", "permissions", "scope", "readonly", "duplicate"])
async def test_role_options_rejects_invalid_public_output(role_case, failure):
    case = role_case
    result = await case.service.list_options(
        actor=case.actor, access_context=case.access, scope_type="tenant", scope_id=case.actor.tenant_id,
    )
    if failure == "private":
        result["token"] = "private-role-secret"
    elif failure == "permissions":
        result["roles"][0]["permissions"] = ["unregistered.permission"]
    elif failure == "scope":
        result["scope_id"] = str(uuid4())
    elif failure == "readonly":
        result["permissions_read_only"] = False
    else:
        result["roles"].append(dict(result["roles"][0]))
    service = SimpleNamespace(list_options=AsyncMock(return_value=result))
    case.app.dependency_overrides[routes.get_role_options_service] = lambda: service
    response = await get(case)
    assert response.status_code == 500, response.text
    assert "private-role-secret" not in response.text
    no_writes(case)

def test_role_options_factory_uses_constructor_session():
    session = SimpleNamespace(in_transaction=lambda: True)
    service = routes.get_role_options_service(session=session)
    assert service.session is session and service.project_repository.session is session
