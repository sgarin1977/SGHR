from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.dialects import postgresql

import api.routes.construction_access_read as routes
from api.auth import get_current_actor
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from database.repositories.construction import ConstructionAccessRepository
from services.construction_grants import ConstructionAccessGrantService
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.module_access import ModuleDisabledError
from test_construction_1a_invitation_acceptance_http import http_case
from test_construction_1a_invitation_management_http import management_case

@pytest.fixture
def access_read_case(management_case):
    case = management_case
    case.target_user_id = uuid4()
    case.now = datetime.now(timezone.utc)
    case.grants = [SimpleNamespace(
        id=uuid4(), tenant_id=case.actor.tenant_id, user_id=case.target_user_id,
        role="ADMIN", scope_type="tenant", scope_id=case.actor.tenant_id,
        status="active", expires_at=None, granted_at=case.now,
        delivery_metadata={"token": "private-read-secret"},
    )]
    case.statements = []
    async def execute(statement):
        case.statements.append(statement)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: case.grants))
    case.session = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    for name in ("commit", "rollback", "flush", "add"):
        setattr(case.session, name, AsyncMock(side_effect=AssertionError("Read must not write.")))
    case.events = SimpleNamespace(create_event=AsyncMock(side_effect=AssertionError("Read must not audit.")))
    case.service = ConstructionAccessGrantService(
        session=case.session, repository=ConstructionAccessRepository(case.session),
        event_repository=case.events, now_provider=lambda: case.now,
    )
    case.app.dependency_overrides[routes.get_access_grant_read_service] = lambda: case.service
    return case

async def get(case, *, anonymous=False, query="", target=None):
    if not anonymous:
        case.app.dependency_overrides[get_current_actor] = lambda: case.actor
    identifier = str(case.target_user_id) if target is None else target
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get(
            "/api/v1/construction/team/members/" + identifier + "/access-grants" + query,
            headers={"X-Request-ID": "access-read-http"},
        )

def no_writes(case):
    for name in ("commit", "rollback", "flush", "add"):
        getattr(case.session, name).assert_not_called()
    case.events.create_event.assert_not_called()

@pytest.mark.asyncio
async def test_access_read_reuses_service_and_scopes_sql_to_actor_and_target(access_read_case):
    case = access_read_case
    response = await get(case, query="?tenant_id=" + str(uuid4()))
    assert response.status_code == 200, response.text
    assert response.json()["request_id"] == "access-read-http"
    row = response.json()["data"][0]
    assert set(row) == {"id", "user_id", "role", "scope_type", "scope_id", "status", "expires_at", "granted_at"}
    assert row["user_id"] == str(case.target_user_id) and "private-read-secret" not in response.text
    assert len(case.statements) == 1
    statement = case.statements[0]
    values = statement.compile().params.values()
    assert case.actor.tenant_id in values and case.target_user_id in values
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in sql and "expires_at >" in sql and "status =" in sql
    no_writes(case)

@pytest.mark.asyncio
async def test_access_read_empty_list_is_success(access_read_case):
    case = access_read_case
    case.grants = []
    response = await get(case)
    assert response.status_code == 200 and response.json()["data"] == []
    no_writes(case)

@pytest.mark.asyncio
async def test_access_read_project_filter_returns_direct_project_assignments(access_read_case):
    case = access_read_case
    project = uuid4()
    case.grants[0].scope_type = "project"
    case.grants[0].scope_id = project
    case.grants[0].role = "ESTIMATOR"
    response = await get(case, query="?project_id=" + str(project))
    assert response.status_code == 200, response.text
    statement = case.statements[0]
    params = statement.compile().params
    assert project in params.values() and "project" in params.values()
    assert response.json()["data"][0]["scope_id"] == str(project)
    no_writes(case)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    ("anonymous", 401), ("inactive", 403), ("team_only", 403),
    ("foreign_context", 403), ("module", 403), ("user_id", 422), ("project_id", 422),
])
async def test_access_read_denies_before_grant_query(access_read_case, failure, status):
    case = access_read_case
    query, target = "", None
    if failure == "inactive":
        case.actor.status = "suspended"
    elif failure == "team_only":
        case.access = ConstructionAccessContext(
            user_id=case.actor.user_id, tenant_id=case.actor.tenant_id,
            roles=("PROJECT_MANAGER",), permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
            grants=(ConstructionGrantContext("PROJECT_MANAGER", "project", uuid4()),),
        )
    elif failure == "foreign_context":
        case.access = ConstructionAccessContext(
            user_id=case.actor.user_id, tenant_id=uuid4(), roles=case.access.roles,
            permissions=case.access.permissions, grants=case.access.grants,
        )
    elif failure == "module":
        case.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "user_id":
        target = "invalid"
    elif failure == "project_id":
        query = "?project_id=invalid"
    response = await get(case, anonymous=failure == "anonymous", query=query, target=target)
    assert response.status_code == status, response.text
    assert case.statements == []
    no_writes(case)

@pytest.mark.asyncio
@pytest.mark.parametrize("query_kind", ["none", "foreign", "own"])
async def test_access_read_enforces_access_manager_project_scope(access_read_case, query_kind):
    case = access_read_case
    own = uuid4()
    case.access = ConstructionAccessContext(
        user_id=case.actor.user_id, tenant_id=case.actor.tenant_id, roles=("ADMIN",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["ADMIN"]),
        grants=(ConstructionGrantContext("ADMIN", "project", own),),
    )
    case.grants[0].scope_type, case.grants[0].scope_id = "project", own
    query = "" if query_kind == "none" else "?project_id=" + str(own if query_kind == "own" else uuid4())
    response = await get(case, query=query)
    assert response.status_code == (200 if query_kind == "own" else 403), response.text
    assert len(case.statements) == (1 if query_kind == "own" else 0)
    no_writes(case)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "user", "revoked", "scope", "role", "duplicate"])
async def test_access_read_rejects_invalid_repository_scope_without_leaking_data(access_read_case, failure):
    case = access_read_case
    if failure == "tenant":
        case.grants[0].tenant_id = uuid4()
    elif failure == "user":
        case.grants[0].user_id = uuid4()
    elif failure == "revoked":
        case.grants[0].status = "revoked"
    elif failure == "scope":
        case.grants[0].scope_id = uuid4()
    elif failure == "role":
        case.grants[0].role = "UNREGISTERED"
    else:
        case.grants.append(case.grants[0])
    response = await get(case)
    assert response.status_code == 500, response.text
    assert "private-read-secret" not in response.text
    no_writes(case)

def test_access_read_factory_keeps_repository_and_audit_on_constructor_session():
    session = object()
    service = routes.get_access_grant_read_service(session=session)
    assert service.session is session and service.repository.session is session
    assert service.audit_logger.event_repository.session is session

def test_access_read_openapi_requires_no_version_or_idempotency(access_read_case):
    path = "/api/v1/construction/team/members/{user_id}/access-grants"
    operation = access_read_case.app.openapi()["paths"][path]["get"]
    assert not any(item["name"] in ("Idempotency-Key", "expected_row_version", "tenant_id") for item in operation["parameters"])
