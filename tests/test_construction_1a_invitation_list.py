from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.dialects import postgresql

import api.routes.construction_invitation_list as routes
from api.auth import get_current_actor
from database.repositories.construction_invitation_list import ConstructionInvitationListRepository
from services.construction_invitation_list import ConstructionInvitationListService
from services.construction_invitations import ConstructionInvitationOperationError
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.module_access import ModuleDisabledError
from test_construction_1a_invitation_acceptance_http import http_case
from test_construction_1a_invitation_management_http import management_case

@pytest.fixture
def list_case():
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4(), status="active")
    access = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id, roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", actor.tenant_id),),
    )
    row = {
        "id": uuid4(), "tenant_id": actor.tenant_id, "email": "person@example.com",
        "role": "ADMIN", "status": "pending", "expires_at": now + timedelta(days=7),
        "grant_expires_at": None, "row_version": 6,
        "scope_type": "tenant", "scope_id": actor.tenant_id,
    }
    case = SimpleNamespace(actor=actor, access=access, now=now, rows=[row], statements=[])
    async def execute(statement):
        case.statements.append(statement)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: case.rows))
    session = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    for name in ("commit", "rollback", "flush", "add"):
        setattr(session, name, AsyncMock(side_effect=AssertionError("List must not write.")))
    case.session = session
    case.service = ConstructionInvitationListService(
        session=session, repository=ConstructionInvitationListRepository(session), now_provider=lambda: case.now,
    )
    return case

async def read(case, project_id=None):
    return await case.service.list_invitations(actor=case.actor, access_context=case.access, project_id=project_id)

def assert_no_writes(case):
    for name in ("commit", "rollback", "flush", "add"):
        getattr(case.session, name).assert_not_called()

@pytest.mark.asyncio
async def test_list_one_select_projects_only_public_columns_and_no_locks(list_case):
    case = list_case
    output = await read(case)
    assert len(output) == 1 and output[0]["row_version"] == 6
    assert len(case.statements) == 1
    statement = case.statements[0]
    assert set(statement.selected_columns.keys()) == {
        "id", "tenant_id", "email", "role", "status", "expires_at",
        "grant_expires_at", "row_version", "scope_type", "scope_id",
    }
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN" in sql and "FOR UPDATE" not in sql
    assert "token_hash" not in sql and "delivery_metadata" not in sql
    assert "construction_invitation_scopes.tenant_id = construction_invitations.tenant_id" in sql
    assert list(statement.compile().params.values()) == [case.actor.tenant_id]
    assert "created_at DESC" in sql
    assert_no_writes(case)

@pytest.mark.asyncio
@pytest.mark.parametrize("stored,expired,expected", [
    ("pending", False, "pending"), ("pending", True, "expired"),
    ("accepted", True, "accepted"), ("declined", True, "declined"),
    ("revoked", True, "revoked"), ("expired", True, "expired"),
])
async def test_list_resolves_status_without_mutating_history(list_case, stored, expired, expected):
    case = list_case
    case.rows[0]["status"] = stored
    if expired:
        case.rows[0]["expires_at"] = case.now
    original = dict(case.rows[0])
    output = await read(case)
    assert output[0]["status"] == expected
    assert case.rows[0] == original
    assert_no_writes(case)

@pytest.mark.asyncio
async def test_list_aggregates_all_scopes_into_one_invitation(list_case):
    case = list_case
    first, second = uuid4(), uuid4()
    original = {**case.rows[0], "role": "ESTIMATOR", "scope_type": "project", "scope_id": first}
    case.rows = [original, {**original, "scope_id": second}]
    output = await read(case)
    assert len(output) == 1
    assert output[0]["scopes"] == [
        {"scope_type": "project", "scope_id": str(first)},
        {"scope_type": "project", "scope_id": str(second)},
    ]
    assert_no_writes(case)

def project_context(case, *projects):
    return ConstructionAccessContext(
        user_id=case.actor.user_id, tenant_id=case.actor.tenant_id, roles=("PROJECT_MANAGER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["PROJECT_MANAGER"]),
        grants=tuple(ConstructionGrantContext("PROJECT_MANAGER", "project", target) for target in projects),
    )

@pytest.mark.asyncio
async def test_list_project_manager_sees_only_fully_authorized_invitations(list_case):
    case = list_case
    allowed, foreign = uuid4(), uuid4()
    case.access = project_context(case, allowed)
    visible = {**case.rows[0], "role": "ESTIMATOR", "scope_type": "project", "scope_id": allowed}
    mixed = {**visible, "id": uuid4(), "email": "hidden@example.com"}
    case.rows = [visible, mixed, {**mixed, "scope_id": foreign}]
    output = await read(case, allowed)
    assert [item["id"] for item in output] == [str(visible["id"])]
    assert "hidden@example.com" not in repr(output)
    statement = case.statements[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "EXISTS" in sql and "invitation_target" in sql
    assert set(statement.compile().params.values()).issuperset({case.actor.tenant_id, allowed})
    assert_no_writes(case)

@pytest.mark.asyncio
async def test_list_project_manager_with_all_scopes_sees_complete_invitation(list_case):
    case = list_case
    first, second = uuid4(), uuid4()
    case.access = project_context(case, first, second)
    original = {**case.rows[0], "role": "ESTIMATOR", "scope_type": "project", "scope_id": first}
    case.rows = [original, {**original, "scope_id": second}]
    output = await read(case, first)
    assert len(output) == 1 and len(output[0]["scopes"]) == 2

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inactive", "foreign_context", "no_permission", "project_without_filter"])
async def test_list_permission_denial_precedes_repository_read(list_case, failure):
    case = list_case
    error = ConstructionPermissionDeniedError
    if failure == "inactive":
        case.actor.status = "suspended"
    elif failure == "foreign_context":
        case.access = ConstructionAccessContext(
            user_id=case.actor.user_id, tenant_id=uuid4(), roles=case.access.roles,
            permissions=case.access.permissions, grants=case.access.grants,
        )
    elif failure == "no_permission":
        case.access = ConstructionAccessContext(
            user_id=case.actor.user_id, tenant_id=case.actor.tenant_id, roles=("OBSERVER",),
            permissions=(), grants=(),
        )
    else:
        case.access = project_context(case, uuid4())
        error = ConstructionScopeDeniedError
    with pytest.raises(error):
        await read(case)
    assert case.statements == []
    assert_no_writes(case)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign_tenant", "missing_scope", "duplicate_scope", "document", "version", "inconsistent"])
async def test_list_rejects_corrupt_snapshot_without_writes(list_case, failure):
    case = list_case
    if failure == "foreign_tenant":
        case.rows[0]["tenant_id"] = uuid4()
    elif failure == "missing_scope":
        case.rows[0]["scope_type"], case.rows[0]["scope_id"] = None, None
    elif failure == "duplicate_scope":
        case.rows.append(dict(case.rows[0]))
    elif failure == "document":
        case.rows[0]["scope_type"] = "document"
    elif failure == "version":
        case.rows[0]["row_version"] = True
    else:
        case.rows.append({**case.rows[0], "email": "changed@example.com"})
    with pytest.raises(ConstructionInvitationOperationError):
        await read(case)
    assert_no_writes(case)

@pytest.fixture
def list_http(management_case):
    case = management_case
    output = {**case.management.create_invitation.return_value, "status": "expired"}
    case.list_service = SimpleNamespace(list_invitations=AsyncMock(return_value=[output]))
    case.app.dependency_overrides[routes.get_invitation_list_service] = lambda: case.list_service
    return case

async def get(case, *, anonymous=False, query=""):
    if not anonymous:
        case.app.dependency_overrides[get_current_actor] = lambda: case.actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get(
            "/api/v1/construction/invitations" + query, headers={"X-Request-ID": "invitation-list-http"},
        )

@pytest.mark.asyncio
async def test_list_http_needs_no_idempotency_key_and_uses_canonical_actor(list_http):
    case = list_http
    response = await get(case, query="?tenant_id=" + str(uuid4()))
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["status"] == "expired"
    assert response.json()["request_id"] == "invitation-list-http"
    case.list_service.list_invitations.assert_awaited_once_with(
        actor=case.actor, access_context=case.access, project_id=None,
    )

@pytest.mark.asyncio
async def test_list_http_forwards_valid_project_filter(list_http):
    project = uuid4()
    response = await get(list_http, query="?project_id=" + str(project))
    assert response.status_code == 200, response.text
    assert list_http.list_service.list_invitations.await_args.kwargs["project_id"] == project

@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    ("anonymous", 401), ("module", 403), ("permission", 403),
    ("project", 422), ("scope", 403), ("private", 500), ("nested_private", 500),
])
async def test_list_http_denials_and_secret_redaction(list_http, failure, status):
    case = list_http
    query = ""
    if failure == "module":
        case.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "permission":
        case.access = ConstructionAccessContext(
            user_id=case.actor.user_id, tenant_id=case.actor.tenant_id,
            roles=("OBSERVER",), permissions=(), grants=(),
        )
    elif failure == "project":
        query = "?project_id=invalid"
    elif failure == "scope":
        case.list_service.list_invitations.side_effect = ConstructionScopeDeniedError()
    elif failure in ("private", "nested_private"):
        output = dict(case.list_service.list_invitations.return_value[0])
        if failure == "private":
            output["token"] = "private-list-secret"
        else:
            output["scopes"] = [{**output["scopes"][0], "token_hash": "private-list-secret"}]
        case.list_service.list_invitations.return_value = [output]
    response = await get(case, anonymous=failure == "anonymous", query=query)
    assert response.status_code == status, response.text
    assert "private-list-secret" not in response.text
    if failure in ("anonymous", "module", "permission", "project"):
        case.list_service.list_invitations.assert_not_awaited()

@pytest.mark.asyncio
async def test_list_empty_result_is_read_only(list_case):
    list_case.rows = []
    assert await read(list_case) == []
    assert len(list_case.statements) == 1
    assert_no_writes(list_case)

@pytest.mark.asyncio
async def test_list_project_manager_cannot_see_tenant_wide_invitation(list_case):
    case = list_case
    project = uuid4()
    case.access = project_context(case, project)
    assert await read(case, project) == []
    assert_no_writes(case)

@pytest.mark.asyncio
async def test_list_admin_reads_history_without_role_delegation_check(list_case):
    case = list_case
    case.access = ConstructionAccessContext(
        user_id=case.actor.user_id, tenant_id=case.actor.tenant_id, roles=("ADMIN",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["ADMIN"]),
        grants=(ConstructionGrantContext("ADMIN", "tenant", case.actor.tenant_id),),
    )
    case.rows[0]["role"] = "OWNER"
    assert (await read(case))[0]["role"] == "OWNER"
    assert_no_writes(case)
