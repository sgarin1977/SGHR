from construction_rate_limit_fixture import with_local_construction_rate_limit
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import api.routes.construction_activities as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.construction_activity_rules import ACTIVITY_RESULT_UNSET
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_activity_create_service import activity_create_case
from test_construction_1a_activity_create_workflow import workflow_case as create_workflow_case
from test_construction_1a_activity_terminal_service import terminal_case
from test_construction_1a_activity_terminal_workflow import workflow_case as terminal_workflow_case


@pytest.fixture
def http_case(request, create_workflow_case, terminal_workflow_case):
    operation = getattr(request, "param", "create")
    c = create_workflow_case if operation == "create" else terminal_workflow_case
    c.operation = operation
    c.foreign = uuid4()
    if operation != "create":
        c.row.result = "  Existing result  "
        for name in ("complete_activity", "cancel_activity"):
            original = getattr(c.repo, name).side_effect
            async def preserve(original=original, **kwargs):
                if kwargs["result"] is ACTIVITY_RESULT_UNSET:
                    kwargs["result"] = c.row.result
                return await original(**kwargs)
            getattr(c.repo, name).side_effect = preserve
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.module = SimpleNamespace(require_enabled=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    dependency = routes.get_activity_create_workflow if operation == "create" else routes.get_activity_terminal_workflow
    c.app.dependency_overrides[dependency] = lambda: c.workflow
    return c


def body_for(c):
    if c.operation == "create":
        return dict(type=" CALL ", due_at=c.now.isoformat(), timezone=" Europe/Lisbon ",
                    lead_id=str(c.lead.id), comment=" Private comment ")
    return dict(expected_row_version=4)


async def post(c, *, body=None, key="activity-http-private-key", headers=None, identifier=None):
    pairs = [("X-Request-ID", "activity-http-test"), ("X-Tenant-ID", str(c.foreign))]
    if key is not None: pairs.append(("Idempotency-Key", key))
    pairs.extend(headers or [])
    path = "/api/v1/construction/crm/activities"
    if c.operation != "create": path += "/" + (identifier or str(c.row.id)) + "/" + c.operation
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
                                 base_url="http://testserver") as client:
        return await client.post(path, json=body_for(c) if body is None else body, headers=pairs,
                                 params={"tenant_id": str(c.foreign)})


def no_write(c):
    for name in ("create_activity", "complete_activity", "cancel_activity"):
        method = getattr(c.repo, name, None)
        if method is not None: method.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.parametrize("http_case", ["create", "complete", "cancel"], indirect=True)
async def test_registered_http_calls_real_workflow_and_replays_without_second_audit(http_case):
    c = http_case
    first = await post(c)
    assert first.status_code == (201 if c.operation == "create" else 200), first.text
    data = first.json()["data"]
    assert data["tenant_id"] == str(c.tenant) and data["id"] == str(c.row.id)
    assert data["status"] == {"create": "planned", "complete": "completed", "cancel": "cancelled"}[c.operation]
    assert data["row_version"] == (1 if c.operation == "create" else 5)
    assert first.json()["request_id"] == first.headers["X-Request-ID"] == "activity-http-test"
    assert first.json()["meta"] == {}
    assert not any(secret in first.text for secret in ("key_hash", "ciphertext", "binding", "private-internal", "never-public", "activity-http-private-key"))
    assert c.events.create_event.await_args.kwargs["trace_id"] == "activity-http-test"
    assert c.events.create_event.await_args.kwargs["tenant_id"] == c.tenant
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")
    assert c.order.index("audit") < c.order.index("complete") if c.operation == "create" else c.order.index("audit") < c.order.index("commit")
    c.row.comment = "Later change"
    c.row.row_version += 1
    second = await post(c)
    assert second.status_code == first.status_code, second.text
    assert second.json()["data"] == data
    c.events.create_event.assert_awaited_once()
    getattr(c.repo, c.operation + "_activity").assert_awaited_once()


@pytest.mark.parametrize("http_case", ["complete", "cancel"], indirect=True)
@pytest.mark.parametrize("supplied,expected", [(False, "  Existing result  "), (None, None), (" New result ", "New result")])
async def test_http_preserves_omission_and_nullable_result_presence(http_case, supplied, expected):
    c = http_case
    body = body_for(c)
    if supplied is not False: body["result"] = supplied
    response = await post(c, body=body)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["result"] == expected
    assert c.row.result == expected
    assert c.events.create_event.await_args.kwargs["payload"]["result_changed"] is (supplied is not False)


@pytest.mark.parametrize("http_case", ["complete", "cancel"], indirect=True)
async def test_http_same_key_omitted_result_then_explicit_null_conflicts(http_case):
    c = http_case
    assert (await post(c)).status_code == 200
    response = await post(c, body=dict(expected_row_version=4, result=None))
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    c.events.create_event.assert_awaited_once()


async def test_creation_hash_preserves_supplied_whitespace(http_case):
    c = http_case
    assert (await post(c)).status_code == 201
    body = body_for(c); body["type"] = "call"
    response = await post(c, body=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize("http_case", ["create", "complete", "cancel"], indirect=True)
@pytest.mark.parametrize("key", [None, "", "   ", "x"*256])
async def test_key_is_required_before_reservation_or_mutation(http_case, key):
    c = http_case
    response = await post(c, key=key)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    assert not c.records.rows
    no_write(c)


@pytest.mark.parametrize("http_case", ["create", "complete", "cancel"], indirect=True)
async def test_duplicate_key_headers_are_rejected(http_case):
    c = http_case
    response = await post(c, headers=[("Idempotency-Key", "second")])
    assert response.status_code == 422
    assert not c.records.rows
    no_write(c)


@pytest.mark.parametrize("http_case", ["create", "complete", "cancel"], indirect=True)
@pytest.mark.parametrize("failure", ["anonymous", "inactive", "permission", "tenant", "suite", "module", "membership", "revoked"])
async def test_http_trusted_auth_entitlements_and_current_membership_before_writes(http_case, failure):
    c = http_case
    if failure == "anonymous":
        def anonymous(): raise ApiHttpError(status_code=401, code="authentication_required", message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = anonymous
    elif failure == "inactive": c.actor.status = "blocked"
    elif failure in ("permission", "tenant"):
        c.context = ConstructionAccessContext(c.user, c.foreign if failure == "tenant" else c.tenant,
            ("OWNER",), () if failure == "permission" else c.context.permissions,
            (ConstructionGrantContext("OWNER", "tenant", c.tenant),))
    elif failure in ("suite", "module"):
        (c.suite if failure == "suite" else c.module).require_enabled.side_effect = SuiteDisabledError() if failure == "suite" else ModuleDisabledError()
    elif failure == "membership": c.access.has_active_platform_membership.return_value = False
    else: c.grant.status = "revoked"
    response = await post(c)
    assert response.status_code == (401 if failure == "anonymous" else 403), response.text
    if failure in ("suite", "module"): assert response.json()["error"]["code"] == "MODULE_DISABLED"
    no_write(c)


@pytest.mark.parametrize("field,value", [
    ("type", "invalid"), ("type", True), ("due_at", "2026-10-13T12:00:00"), ("due_at", 1),
    ("timezone", "Not/AZone"), ("timezone", None), ("lead_id", None), ("lead_id", "invalid"),
    ("tenant_id", str(uuid4())), ("status", "completed"), ("result", "not-on-create"),
    ("expected_row_version", 1), ("comment", 42),
])
async def test_create_request_uses_only_approved_model_fields(http_case, field, value):
    c = http_case; body = body_for(c); body[field] = value
    response = await post(c, body=body)
    assert response.status_code == 422, response.text
    no_write(c)


@pytest.mark.parametrize("http_case", ["complete", "cancel"], indirect=True)
@pytest.mark.parametrize("body", [{}, {"expected_row_version": True}, {"expected_row_version": "4"},
    {"expected_row_version": 0}, {"expected_row_version": 2**63}, {"expected_row_version": 4, "result": 42},
    {"expected_row_version": 4, "status": "completed"}, {"expected_row_version": 4, "tenant_id": str(uuid4())},
    {"expected_row_version": 4, "row_version": 5}, {"expected_row_version": 4, "project_id": str(uuid4())}])
async def test_terminal_only_expected_version_and_optional_result_are_accepted(http_case, body):
    response = await post(http_case, body=body)
    assert response.status_code == 422, response.text
    no_write(http_case)


@pytest.mark.parametrize("http_case", ["complete", "cancel"], indirect=True)
@pytest.mark.parametrize("failure,code,status", [("stale", "ROW_VERSION_CONFLICT", 409),
    ("terminal", "conflict", 409), ("missing", "not_found", 404), ("foreign", "not_found", 404),
    ("audit", "internal_error", 500), ("commit", "internal_error", 500)])
async def test_terminal_domain_errors_are_sanitized_and_workflow_rolls_back(http_case, failure, code, status):
    c = http_case
    if failure == "stale": c.row.row_version = 5
    elif failure == "terminal": c.row.status = "completed"
    elif failure == "missing": c.repo.find_active_activity_links.side_effect = None; c.repo.find_active_activity_links.return_value = None
    elif failure == "foreign": c.row.tenant_id = c.foreign
    elif failure == "audit": c.events.create_event.side_effect = RuntimeError("private audit details")
    else: c.session.commit.side_effect = RuntimeError("private commit details")
    response = await post(c)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code and "private" not in response.text
    c.session.rollback.assert_awaited_once()


@pytest.mark.parametrize("http_case", ["create", "complete", "cancel"], indirect=True)
async def test_processing_reservation_never_repeats_write(http_case):
    c = http_case
    await post(c)
    saved = next(iter(c.records.rows.values()))
    saved.status = "processing"; saved.response_status = None; saved.response_ciphertext = None
    response = await post(c)
    assert response.status_code == 409 and response.json()["error"]["code"] == "idempotency_in_progress", response.text
    c.events.create_event.assert_awaited_once()


@pytest.mark.parametrize("http_case", ["complete", "cancel"], indirect=True)
async def test_invalid_path_uuid_is_not_echoed(http_case):
    response = await post(http_case, identifier="private-invalid-uuid")
    assert response.status_code == 422 and "private-invalid-uuid" not in response.text
    no_write(http_case)


@pytest.mark.parametrize("http_case", ["create", "complete", "cancel"], indirect=True)
@pytest.mark.parametrize("fault", ["tenant", "id_object", "secret", "version", "status"])
async def test_invalid_or_foreign_workflow_projection_is_not_published(http_case, fault):
    c = http_case
    first = await post(c)
    data = first.json()["data"]
    if fault == "tenant": data["tenant_id"] = str(c.foreign)
    elif fault == "id_object": data["id"] = c.row.id
    elif fault == "secret": data["secret"] = "private-output"
    elif fault == "version": data["row_version"] = 8
    else: data["status"] = "cancelled" if c.operation != "cancel" else "completed"
    fake = SimpleNamespace(**{c.operation + "_activity": AsyncMock(return_value=data)})
    dependency = routes.get_activity_create_workflow if c.operation == "create" else routes.get_activity_terminal_workflow
    c.app.dependency_overrides[dependency] = lambda: fake
    response = await post(c)
    assert response.status_code == 500 and "private-output" not in response.text, response.text


def test_registered_openapi_exposes_exact_three_approved_posts(http_case):
    api = http_case.app.openapi()
    prefix = "/api/v1/construction/crm/activities"
    paths = {path: spec for path, spec in api["paths"].items() if path.startswith(prefix)}
    assert set(paths) == {prefix, prefix+"/{id}/complete", prefix+"/{id}/cancel"}
    for path, spec in paths.items():
        assert set(spec) == {"post"}
        operation = spec["post"]
        assert sum(p["in"] == "header" and p["name"] == "Idempotency-Key" and p["required"] for p in operation["parameters"]) == 1
        assert ("201" if path == prefix else "200") in operation["responses"]
    schema = api["components"]["schemas"]["ActivityTerminalRequest"]
    assert set(schema["required"]) == {"expected_row_version"}
    assert set(schema["properties"]) == {"expected_row_version", "result"}
    assert schema["additionalProperties"] is False


def test_workflow_factories_reuse_scoped_session_and_encrypted_platform_idempotency(monkeypatch):
    session = object()
    settings = SimpleNamespace(encryption_key="private-encryption-key")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", lambda: settings)
    for name, build in [("get_activity_create_workflow", "build_construction_activity_create_workflow"),
                        ("get_activity_terminal_workflow", "build_construction_activity_terminal_workflow")]:
        calls = []
        monkeypatch.setattr(routes, build, lambda **kw: calls.append(kw) or session)
        factory = getattr(routes, name)
        assert factory.__defaults__[0].dependency is get_construction_scoped_session
        assert factory(session=session, key="key") is session
        assert calls == [dict(session=session, encryption_key=settings.encryption_key)]
