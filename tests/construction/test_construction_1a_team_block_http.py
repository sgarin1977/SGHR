from construction_rate_limit_fixture import with_local_construction_rate_limit
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import api.routes.construction_team_block as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_team_block_service import block_case, snapshot
from test_construction_1a_team_block_workflow import block_pipeline


MISSING = object()


@pytest.fixture
def block_http(block_pipeline):
    c = block_pipeline
    c.app = with_local_construction_rate_limit(create_app())
    c.http_access = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, ("OWNER",),
        tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"])),
        (ConstructionGrantContext("OWNER", "tenant", c.actor.tenant_id),),
    )
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.http_access
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_team_block_workflow] = lambda: c.workflow
    return c


async def post(c, *, body=MISSING, identifier=None):
    if not c.db.in_transaction:
        c.db.execute("BEGIN")
    headers = {"X-Request-ID": "team-block-http"}
    fields = {}
    if body is None:
        fields["content"] = "null"
        headers["Content-Type"] = "application/json"
    elif body is not MISSING:
        fields["json"] = body
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="http://testserver",
    ) as client:
        return await client.post(
            "/api/v1/construction/team/members/" + (identifier or str(c.target)) + "/block"
            + "?tenant_id=" + str(c.foreign_tenant),
            headers=headers, **fields,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [MISSING, None, {}])
async def test_http_block_uses_real_workflow_actor_tenant_and_one_commit(block_http, body):
    c = block_http
    before = snapshot(c)
    response = await post(c, body=body)
    assert response.status_code == 200, response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"] == "team-block-http"
    assert response.json()["data"] == {
        "user_id": str(c.target),
        "revoked_grant_ids": [str(value) for value in sorted(c.expected, key=lambda value: value.int)],
    }
    after = snapshot(c)
    assert after["users"] == before["users"] and after["memberships"] == before["memberships"]
    assert len(after["events"]) == 3
    assert all(row[2] == str(c.actor.tenant_id) and row[5] == "team-block-http" for row in after["events"])
    foreign_before = [row for row in before["grants"] if row[1] == str(c.foreign_tenant)]
    assert [row for row in after["grants"] if row[1] == str(c.foreign_tenant)] == foreign_before
    assert "row_version" not in response.text and "blocked" not in response.json()["data"]
    c.session.commit.assert_awaited_once()
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.actor.tenant_id, suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.actor.tenant_id, module_code="construction.core")


@pytest.mark.asyncio
async def test_http_repeat_returns_empty_changes_without_extra_audit(block_http):
    c = block_http
    first = await post(c)
    before = snapshot(c)
    second = await post(c)
    assert first.status_code == second.status_code == 200
    assert second.json()["data"] == {"user_id": str(c.target), "revoked_grant_ids": []}
    assert snapshot(c) == before and c.event_repo.create_event.await_count == 3
    assert c.session.commit.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    ("anonymous", 401), ("inactive", 403), ("permission", 403), ("foreign_context", 403),
    ("suite", 403), ("module", 403), ("project_only", 403), ("lost_authority", 403),
])
async def test_http_block_denies_before_writes(block_http, failure, status):
    c = block_http
    if failure == "anonymous":
        c.app.dependency_overrides.pop(get_current_actor)
    elif failure == "inactive":
        c.actor.status = "suspended"
    elif failure in ("permission", "foreign_context"):
        c.http_access = ConstructionAccessContext(
            c.actor.user_id, uuid4() if failure == "foreign_context" else c.actor.tenant_id,
            ("OBSERVER",), (), (),
        )
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "project_only":
        project_id = uuid4()
        c.http_access = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("ADMIN",),
            tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS["ADMIN"])),
            (ConstructionGrantContext("ADMIN", "project", project_id),),
        )
        c.db.execute("UPDATE grants SET role='ADMIN', scope_type='project', scope_id=? WHERE id=?",
                     (str(project_id), str(c.authority_id)))
        c.db.commit()
    else:
        c.db.execute("UPDATE grants SET status='revoked' WHERE id=?", (str(c.authority_id),))
        c.db.commit()
    before = snapshot(c)
    response = await post(c)
    assert response.status_code == status, response.text
    assert snapshot(c) == before
    c.repo.revoke_effective_user_grants.assert_not_called()
    c.event_repo.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"tenant_id": str(uuid4())}, {"user_id": str(uuid4())}, {"scope_type": "project"},
    {"expected_row_version": 1}, {"row_version": 1}, {"role": "OWNER"},
    {"token": "private-request-secret"}, [], "private-request-secret", True,
])
async def test_http_block_rejects_body_fields_before_mutation(block_http, body):
    c = block_http
    before = snapshot(c)
    response = await post(c, body=body)
    assert response.status_code == 422 and "private-request-secret" not in response.text
    assert snapshot(c) == before
    c.repo.revoke_effective_user_grants.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_http_invalid_target_uuid_is_safe_and_does_not_mutate(block_http):
    c = block_http
    before = snapshot(c)
    response = await post(c, identifier="private-invalid-uuid")
    assert response.status_code == 422 and "private-invalid-uuid" not in response.text
    assert snapshot(c) == before
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit"])
async def test_http_block_failure_rolls_back_without_private_diagnostics(block_http, failure):
    c = block_http
    before = snapshot(c)
    if failure == "commit":
        c.fail_commit = RuntimeError
    else:
        original = c.event_repo.create_event.side_effect
        async def fail(**fields):
            await original(**fields)
            if c.events_attempted == 2:
                raise RuntimeError("private audit diagnostic")
        c.event_repo.create_event.side_effect = fail
    response = await post(c)
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert "private" not in response.text and "diagnostic" not in response.text
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"] == "team-block-http"
    assert snapshot(c) == before
    if failure == "audit":
        c.session.commit.assert_not_called()
    else:
        c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_authority_expiry_during_update_is_forbidden_and_rolled_back(block_http):
    c = block_http
    c.db.execute("UPDATE grants SET expires_at=? WHERE id=?", (
        (c.now+timedelta(seconds=1)).isoformat(), str(c.authority_id),
    ))
    c.db.commit()
    before = snapshot(c)
    original = c.repo.revoke_effective_user_grants.side_effect
    async def wait(**fields):
        result = await original(**fields)
        c.now += timedelta(seconds=1)
        return result
    c.repo.revoke_effective_user_grants.side_effect = wait
    response = await post(c)
    assert response.status_code == 403 and snapshot(c) == before
    c.event_repo.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extra", "target", "id", "duplicate", "order", "shape"])
async def test_http_invalid_workflow_projection_is_generic_and_secret_free(block_http, failure):
    c = block_http
    result = {
        "user_id": str(c.target),
        "revoked_grant_ids": [str(value) for value in sorted(c.expected, key=lambda value: value.int)],
    }
    if failure == "extra":
        result["secret"] = "private-response-secret"
    elif failure == "target":
        result["user_id"] = str(uuid4())
    elif failure == "id":
        result["revoked_grant_ids"] = ["private-response-secret"]
    elif failure == "duplicate":
        result["revoked_grant_ids"] = [result["revoked_grant_ids"][0]]*2
    elif failure == "order":
        result["revoked_grant_ids"].reverse()
    else:
        result["revoked_grant_ids"] = tuple(result["revoked_grant_ids"])
    c.app.dependency_overrides[routes.get_team_block_workflow] = lambda: SimpleNamespace(
        block_access=AsyncMock(return_value=result),
    )
    before = snapshot(c)
    response = await post(c)
    assert response.status_code == 500 and "private-response-secret" not in response.text
    assert snapshot(c) == before
    c.session.commit.assert_not_called()


def test_block_http_factory_uses_construction_session_without_extra_configuration(block_http):
    c = block_http
    workflow = routes.get_team_block_workflow(session=c.session)
    assert workflow.session is c.session
    assert workflow.service.repository.session is c.session
    assert workflow.service.audit_logger.event_repository.session is c.session


def test_block_route_registered_once_and_openapi_forbids_extra_body_fields(block_http):
    c = block_http
    path = "/api/v1/construction/team/members/{user_id}/block"
    def registered(router, prefix=""):
        for route in router.routes:
            child = getattr(route, "original_router", None)
            if child is not None:
                context = getattr(route, "include_context", None)
                yield from registered(child, prefix+getattr(context, "prefix", ""))
            else:
                yield prefix+getattr(route, "path", ""), route
    matched = [
        route for name, route in registered(c.app)
        if name == path and "POST" in getattr(route, "methods", ())
    ]
    assert len(matched) == 1
    assert any(
        getattr(dependency.call, "construction_permission_code", None) == "construction.access.manage"
        for dependency in matched[0].dependant.dependencies
    )
    spec = c.app.openapi()
    operation = spec["paths"][path]["post"]
    assert "200" in operation["responses"]
    names = {item["name"] for item in operation["parameters"]}
    assert "user_id" in names and "tenant_id" not in names and "Idempotency-Key" not in names
    request_schema = spec["components"]["schemas"]["TeamBlockRequest"]
    assert request_schema["additionalProperties"] is False and request_schema["properties"] == {}
