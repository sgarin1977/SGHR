from construction_rate_limit_fixture import with_local_construction_rate_limit
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

import api.routes.construction_access_mutations as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from api.settings import ApiConfigurationError
from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_access_mutation_workflow import pipeline, snapshot


@pytest.fixture
def http_case(pipeline):
    c = pipeline
    c.app = with_local_construction_rate_limit(create_app())
    c.http_access = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, ("OWNER",),
        tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        (ConstructionGrantContext("OWNER", "tenant", c.actor.tenant_id),),
    )
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.http_access
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[routes.get_access_mutation_workflow] = lambda: c.workflow
    return c


async def post(c, operation="replace", *, changes=None, key=None, identifier=None, no_body=False, omit_expiry=False):
    if not c.db.in_transaction:
        c.db.execute("BEGIN")
    body = {} if operation == "revoke" else {
        "role": "SURVEYOR", "scope_type": "project", "scope_id": str(c.project_id), "expires_at": None,
    }
    body.update(changes or {})
    if omit_expiry:
        body.pop("expires_at", None)
    headers = {"X-Request-ID": "grant-http"}
    if key is not None:
        headers["Idempotency-Key"] = key
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="http://testserver",
    ) as client:
        return await client.post(
            "/api/v1/construction/team/access-grants/" + (identifier or str(c.original.id)) + "/" + operation
            + "?tenant_id=" + str(uuid4()),
            headers=headers, **({} if no_body else {"json": body}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,key", [("replace", None), ("replace", "key"), ("revoke", None), ("revoke", "key")])
async def test_http_calls_real_workflow_with_actor_tenant_and_one_commit(http_case, operation, key):
    c = http_case
    response = await post(c, operation, key=key)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["request_id"] == response.headers["X-Request-ID"] == "grant-http"
    assert set(payload["data"]) == routes.PUBLIC_GRANT_FIELDS
    assert payload["data"]["status"] == ("active" if operation == "replace" else "revoked")
    assert payload["data"]["user_id"] == str(c.original.user_id)
    assert "never-public" not in response.text and "row_version" not in response.text
    assert all(event.tenant_id == c.actor.tenant_id and event.trace_id == "grant-http" for event in c.rows("audit"))
    c.session.commit.assert_awaited_once()
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.actor.tenant_id, suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.actor.tenant_id, module_code="construction.core")


@pytest.mark.asyncio
async def test_revoke_without_body_is_allowed_and_retry_has_no_new_audit(http_case):
    c = http_case
    first = await post(c, "revoke", no_body=True)
    before = snapshot(c)
    second = await post(c, "revoke", no_body=True)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"] and snapshot(c) == before
    assert len(c.rows("audit")) == 1


@pytest.mark.asyncio
async def test_replace_replay_and_conflicts_preserve_committed_state(http_case):
    c = http_case
    first = await post(c, key="key")
    before = snapshot(c)
    replay = await post(c, key="key")
    assert first.status_code == replay.status_code == 200 and first.json()["data"] == replay.json()["data"]
    assert snapshot(c) == before
    reused = await post(c, key="key", changes={"role": "EXECUTOR"})
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    changed = await post(c, key="other")
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "ACCESS_GRANT_CONFLICT"
    assert snapshot(c) == before and "ROW_VERSION_CONFLICT" not in changed.text


@pytest.mark.asyncio
async def test_replay_after_loss_of_current_rights_is_forbidden(http_case):
    c = http_case
    assert (await post(c, key="key")).status_code == 200
    before = snapshot(c)
    c.authorized = False
    response = await post(c, key="key")
    assert response.status_code == 403 and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    ("anonymous", 401), ("inactive", 403), ("permission", 403),
    ("foreign_context", 403), ("suite", 403), ("module", 403), ("missing_grant", 403),
])
async def test_http_gates_deny_without_mutation(http_case, failure, status):
    c = http_case
    before = snapshot(c)
    identifier = None
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
    else:
        identifier = str(uuid4())
    response = await post(c, identifier=identifier)
    assert response.status_code == status, response.text
    assert snapshot(c) == before and c.session.commit.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replace", "revoke"])
@pytest.mark.parametrize("changes", [
    {"tenant_id": str(uuid4())}, {"user_id": str(uuid4())},
    {"expected_row_version": 1}, {"row_version": 1}, {"token": "private-request-secret"},
])
async def test_mutation_body_rejects_identity_version_and_secret_fields(http_case, operation, changes):
    c = http_case
    before = snapshot(c)
    response = await post(c, operation, changes=changes)
    assert response.status_code == 422 and snapshot(c) == before
    assert "private-request-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"role": "CUSTOM"}, {"scope_type": "document"}, {"scope_id": "invalid"},
    {"expires_at": "2030-01-01T12:00:00"}, {"expires_at": 123}, {"expires_at": True},
])
async def test_replace_validates_complete_target_schema(http_case, changes):
    c = http_case
    before = snapshot(c)
    response = await post(c, changes=changes)
    assert response.status_code == 422 and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid_id", "empty_key", "blank_key", "long_key", "missing_expiry", "past_expiry"])
async def test_mutation_validation_errors_are_safe(http_case, failure):
    c = http_case
    before = snapshot(c)
    changes, key, identifier = {}, None, None
    if failure == "invalid_id":
        identifier = "invalid"
    elif failure in ("empty_key", "blank_key", "long_key"):
        key = {"empty_key": "", "blank_key": "   ", "long_key": "x"*256}[failure]
    elif failure == "missing_expiry":
        response = await post(c, omit_expiry=True)
        assert response.status_code == 422 and snapshot(c) == before
        return
    else:
        changes["expires_at"] = c.now.isoformat()
    response = await post(c, changes=changes, key=key, identifier=identifier)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_http_failure_in_audit_rolls_back_all_rows_without_diagnostics(http_case):
    c = http_case
    before = snapshot(c)
    c.fail_second_audit = True
    response = await post(c, key="key")
    assert response.status_code == 500 and snapshot(c) == before
    assert "private" not in response.text and "diagnostic" not in response.text


@pytest.mark.asyncio
async def test_scoped_manager_cannot_move_grant_to_unrelated_project(http_case):
    c = http_case
    c.http_access = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, ("ADMIN",), tuple(CONSTRUCTION_ROLE_PERMISSIONS["ADMIN"]),
        (ConstructionGrantContext("ADMIN", "project", c.project_id),),
    )
    c.repo.list_active_grants.return_value = [
        SimpleNamespace(role="ADMIN", scope_type="project", scope_id=c.project_id),
    ]
    c.repo.list_active_grants.side_effect = None
    before = snapshot(c)
    response = await post(c, changes={"scope_id": str(uuid4())})
    assert response.status_code == 403 and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extra", "status", "id", "naive_time"])
async def test_http_rejects_invalid_workflow_projection(http_case, failure):
    c = http_case
    result = {
        "id": str(uuid4()), "user_id": str(c.original.user_id), "role": "SURVEYOR",
        "scope_type": "project", "scope_id": str(c.project_id), "status": "active",
        "expires_at": None, "granted_at": c.now.isoformat(),
    }
    if failure == "extra":
        result["secret"] = "private-response-secret"
    elif failure == "status":
        result["status"] = "revoked"
    elif failure == "id":
        result["id"] = str(c.original.id)
    else:
        result["granted_at"] = "2026-10-06T12:00:00"
    c.app.dependency_overrides[routes.get_access_mutation_workflow] = lambda: SimpleNamespace(
        replace_access=AsyncMock(return_value=result),
    )
    response = await post(c)
    assert response.status_code == 500 and "private-response-secret" not in response.text


def test_http_factory_requires_crypto_only_when_key_is_supplied(http_case, monkeypatch):
    c = http_case
    def missing():
        raise ApiConfigurationError("private configuration diagnostic")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", missing)
    without = routes.get_access_mutation_workflow(session=c.session, idempotency_key=None)
    assert without.idempotency is None and without.service.repository.session is c.session
    with pytest.raises(ApiConfigurationError):
        routes.get_access_mutation_workflow(session=c.session, idempotency_key="key")
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", lambda: SimpleNamespace(encryption_key=key))
    with_key = routes.get_access_mutation_workflow(session=c.session, idempotency_key="key")
    assert with_key.idempotency.repository.session is c.session


def test_openapi_records_post_operations_optional_key_and_no_grant_version(http_case):
    spec = http_case.app.openapi()
    for action in ("revoke", "replace"):
        operation = spec["paths"]["/api/v1/construction/team/access-grants/{grant_id}/" + action]["post"]
        parameters = {item["name"]: item for item in operation["parameters"]}
        assert parameters["Idempotency-Key"]["required"] is False
        assert "expected_row_version" not in parameters and "tenant_id" not in parameters
    schema = spec["components"]["schemas"]["GrantReplaceRequest"]
    assert set(schema["required"]) == {"role", "scope_type", "scope_id", "expires_at"}
    assert schema["additionalProperties"] is False and "row_version" not in schema["properties"]
