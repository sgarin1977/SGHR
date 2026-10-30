from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

import api.routes.construction_access_issuance as routes
from api.auth import get_current_actor
from api.settings import ApiConfigurationError
from services.construction.construction_permissions import ConstructionAccessContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_access_mutation_http import http_case, post as mutate_post
from test_construction_1a_access_mutation_workflow import pipeline, snapshot
from test_construction_1a_access_issuance_workflow import issuance_pipeline, overlap


@pytest.fixture
def issuance_http(http_case, issuance_pipeline):
    c = issuance_pipeline
    assert c is http_case
    c.app.dependency_overrides[routes.get_access_issuance_workflow] = lambda: c.issuance_flow
    return c


async def post(c, *, changes=None, key=None, identifier=None):
    if not c.db.in_transaction:
        c.db.execute("BEGIN")
    body = {"role": "SURVEYOR", "scope_type": "project", "scope_id": str(c.project_id)}
    body.update(changes or {})
    headers = {"X-Request-ID": "grant-create-http"}
    if key is not None:
        headers["Idempotency-Key"] = key
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url="http://testserver",
    ) as client:
        return await client.post(
            "/api/v1/construction/team/members/" + (identifier or str(c.original.user_id)) + "/access-grants"
            + "?tenant_id=" + str(uuid4()), json=body, headers=headers,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "create-key"])
async def test_http_creation_uses_real_workflow_and_actor_tenant(issuance_http, key):
    c = issuance_http
    response = await post(c, key=key)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert set(data) == routes.PUBLIC_GRANT_FIELDS and data["status"] == "active"
    assert data["user_id"] == str(c.original.user_id) and data["role"] == "SURVEYOR"
    assert response.json()["request_id"] == response.headers["X-Request-ID"] == "grant-create-http"
    assert data["expires_at"] is None and "never-public" not in response.text
    assert len(c.rows("grant")) == 2 and len(c.rows("audit")) == 1
    assert c.rows("audit")[0].tenant_id == c.actor.tenant_id
    assert c.rows("audit")[0].trace_id == "grant-create-http"
    c.session.commit.assert_awaited_once()
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.actor.tenant_id, suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.actor.tenant_id, module_code="construction.core")


@pytest.mark.asyncio
async def test_creation_replay_and_reused_key_return_correct_status_without_new_rows(issuance_http):
    c = issuance_http
    first = await post(c, key="key")
    before = snapshot(c)
    second = await post(c, key="key")
    assert first.status_code == second.status_code == 201
    assert first.json()["data"] == second.json()["data"] and snapshot(c) == before
    changed = await post(c, key="key", changes={"role": "EXECUTOR"})
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["revoke", "replace"])
async def test_created_grant_works_with_existing_mutation_routes_and_historical_replay(issuance_http, action):
    c = issuance_http
    first = await post(c, key="create-key")
    identifier = first.json()["data"]["id"]
    changes = {"role": "EXECUTOR"} if action == "replace" else {}
    changed = await mutate_post(c, action, identifier=identifier, changes=changes)
    assert changed.status_code == 200, changed.text
    before = snapshot(c)
    replay = await post(c, key="create-key")
    assert replay.status_code == 201 and replay.json()["data"] == first.json()["data"]
    assert snapshot(c) == before
    assert next(g for g in c.rows("grant") if str(g.id) == identifier).status == "revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status", [
    ("anonymous", 401), ("inactive", 403), ("permission", 403), ("foreign_context", 403),
    ("suite", 403), ("module", 403), ("membership", 403), ("invalid_user_id", 422),
])
async def test_issuance_http_denies_without_creation(issuance_http, failure, status):
    c = issuance_http
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
    elif failure == "membership":
        c.repo.has_active_platform_membership.return_value = False
    else:
        identifier = "invalid"
    response = await post(c, identifier=identifier)
    assert response.status_code == status, response.text
    assert snapshot(c) == before and c.session.commit.await_count == 0


@pytest.mark.asyncio
async def test_http_creation_replay_after_loss_of_rights_is_forbidden(issuance_http):
    c = issuance_http
    assert (await post(c, key="key")).status_code == 201
    before = snapshot(c)
    c.authorized = False
    response = await post(c, key="key")
    assert response.status_code == 403 and snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"tenant_id": str(uuid4())}, {"user_id": str(uuid4())}, {"target_user_id": str(uuid4())},
    {"expected_row_version": 1}, {"row_version": 1}, {"token": "private-request-secret"},
    {"role": "CUSTOM"}, {"scope_type": "document"}, {"scope_id": "invalid"},
    {"expires_at": "2030-01-01T12:00:00"}, {"expires_at": 123}, {"expires_at": True},
    {"expires_at": "9999999999"},
])
async def test_issuance_http_rejects_untrusted_or_invalid_body(issuance_http, changes):
    c = issuance_http
    before = snapshot(c)
    response = await post(c, changes=changes)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before and "private-request-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "   ", "x"*256])
async def test_issuance_http_rejects_invalid_optional_key(issuance_http, key):
    c = issuance_http
    before = snapshot(c)
    response = await post(c, key=key)
    assert response.status_code == 422 and snapshot(c) == before


@pytest.mark.asyncio
async def test_http_creation_maps_duplicate_to_grant_conflict(issuance_http):
    c = issuance_http
    before = snapshot(c)
    c.repo.create_grant.side_effect = overlap()
    response = await post(c, key="key")
    assert response.status_code == 409 and response.json()["error"]["code"] == "ACCESS_GRANT_CONFLICT"
    assert snapshot(c) == before and "ROW_VERSION_CONFLICT" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_http_creation_failure_rolls_back_everything_without_diagnostics(issuance_http, failure):
    c = issuance_http
    before = snapshot(c)
    if failure == "audit":
        original = c.service.audit_logger.event_repository.create_event.side_effect
        async def audit(**fields):
            await original(**fields)
            raise RuntimeError("private audit diagnostic")
        c.service.audit_logger.event_repository.create_event.side_effect = audit
    else:
        setattr(c, "fail_complete" if failure == "complete" else "fail_commit", True)
    response = await post(c, key="key")
    assert response.status_code == 500 and snapshot(c) == before
    assert "private" not in response.text and "diagnostic" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["extra", "user", "status", "scope"])
async def test_issuance_http_rejects_invalid_projection_without_secrets(issuance_http, failure):
    c = issuance_http
    result = dict(
        id=str(uuid4()), user_id=str(c.original.user_id), role="SURVEYOR", scope_type="project",
        scope_id=str(c.project_id), status="active", expires_at=None, granted_at=c.now.isoformat(),
    )
    if failure == "extra":
        result["secret"] = "private-response-secret"
    elif failure == "user":
        result["user_id"] = str(uuid4())
    elif failure == "status":
        result["status"] = "revoked"
    else:
        result["scope_id"] = str(uuid4())
    c.app.dependency_overrides[routes.get_access_issuance_workflow] = lambda: SimpleNamespace(
        grant_access=AsyncMock(return_value=result),
    )
    response = await post(c)
    assert response.status_code == 500 and "private-response-secret" not in response.text


def test_http_issuance_factory_requires_crypto_only_for_a_key(issuance_http, monkeypatch):
    c = issuance_http
    def missing():
        raise ApiConfigurationError("private configuration diagnostic")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", missing)
    plain = routes.get_access_issuance_workflow(session=c.session, idempotency_key=None)
    assert plain.idempotency is None and plain.service.repository.session is c.session
    with pytest.raises(ApiConfigurationError):
        routes.get_access_issuance_workflow(session=c.session, idempotency_key="key")
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", lambda: SimpleNamespace(encryption_key=key))
    protected = routes.get_access_issuance_workflow(session=c.session, idempotency_key="key")
    assert protected.idempotency.repository.session is c.session


def test_creation_openapi_coexists_with_read_and_has_no_identity_or_version_body_fields(issuance_http):
    spec = issuance_http.app.openapi()
    path = "/api/v1/construction/team/members/{user_id}/access-grants"
    assert "get" in spec["paths"][path] and "post" in spec["paths"][path]
    operation = spec["paths"][path]["post"]
    assert "201" in operation["responses"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["Idempotency-Key"]["required"] is False
    schema = spec["components"]["schemas"]["GrantCreateRequest"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"role", "scope_type", "scope_id", "expires_at"}
    assert "expires_at" not in schema["required"]


@pytest.mark.asyncio
async def test_http_creation_normalizes_offset_and_replays_after_deadline_without_writes(issuance_http):
    c = issuance_http
    deadline = c.now+timedelta(minutes=1)
    first = await post(
        c, key="key", changes={"expires_at": deadline.astimezone(timezone(timedelta(hours=3))).isoformat()},
    )
    assert first.status_code == 201, first.text
    before = snapshot(c)
    c.now += timedelta(minutes=2)
    second = await post(c, key="key", changes={"expires_at": deadline.isoformat()})
    assert second.status_code == 201 and second.json()["data"] == first.json()["data"]
    assert snapshot(c) == before and c.repo.create_grant.await_count == 1
