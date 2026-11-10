"""CON-018 registered GET API over local SQLite service/repository only."""
import base64
import json
from dataclasses import replace
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import insert, select, update

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service)
from api.errors import ApiHttpError
from api.routes import construction_project_pricing_adjustments as routes
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from api.construction_project_pricing_adjustment_cursors import (
    decode_adjustment_cursor, encode_adjustment_cursor, KIND)
from services.api_rate_limits import (
    ApiRequestRateLimitService, ApiRequestRateLimitExceededError, InMemoryApiRequestRateLimitStore)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_project_pricing_adjustments import (
    ConstructionProjectPricingAdjustmentState as State,
    ConstructionProjectPricingAdjustmentCursor as Cursor,
    ConstructionProjectPricingAdjustmentPage as Page,
    ConstructionProjectPricingAdjustmentValidationError as ValidationError)
from test_construction_02_tax_profiles import case
from test_construction_02_project_pricing_adjustments import adjustment_case, snapshot

PATH = "/api/v1/construction/projects/"


@pytest.fixture
def adjustment_http(adjustment_case):
    c = adjustment_case
    c.sync.rollback()
    c.empty_project = uuid4()
    c.sync.execute(insert(c.projects), dict(
        id=c.empty_project, tenant_id=c.tenant, status="draft", deleted_at=None, row_version=1))
    rows = [
        dict(id=UUID(int=i), tenant_id=c.tenant, project_id=c.project, name="Explicit adjustment",
             coefficient=Decimal("1.125"), enabled=bool(i % 2), row_version=1,
             created_by=c.user, created_at=c.now, updated_at=c.now)
        for i in range(1, 103)]
    rows += [
        rows[0] | dict(id=UUID(int=201), project_id=c.other_project),
        rows[0] | dict(id=UUID(int=301), tenant_id=c.foreign, project_id=c.foreign_project)]
    c.sync.execute(insert(c.adjustments), rows)
    c.sync.commit()
    c.adjustment = State(**rows[0])
    c.adjustment_repo.list_adjustments = AsyncMock(wraps=c.adjustment_repo.list_adjustments)
    c.app = create_app()
    c.suite = SimpleNamespace(require_enabled=AsyncMock())
    c.core = SimpleNamespace(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(wraps=c.rates.ensure_authenticated_user_allowed)

    async def session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(session=Depends(get_construction_scoped_session)):
        assert session is c.session
        return await c.adjustment_service.resolver.resolve(actor=c.actor)

    async def service(session=Depends(get_construction_scoped_session)):
        assert session is c.session
        return c.adjustment_service

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_adjustment_read_service] = service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def call(c, *, project=None, adjustment=None, params=None, query=""):
    path = PATH + str(c.project if project is None else project) + "/pricing-adjustments"
    if adjustment is not None:
        path += "/" + str(adjustment)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.get(path + query, params=params, headers={"X-Request-ID": "adjustment-read-http"})


@pytest.mark.asyncio
async def test_default_page_is_exact_private_bounded_and_read_only(adjustment_http):
    c = adjustment_http
    before, commits = snapshot(c), c.session.commits
    c.sync.rollback()
    response = await call(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_id"] == "adjustment-read-http" and len(body["data"]) == 20
    assert [UUID(row["id"]).int for row in body["data"]] == list(range(1, 21))
    assert {row["enabled"] for row in body["data"]} == {True, False}
    assert all(row["coefficient"] == "1.125" and row["row_version"] == 1 for row in body["data"])
    cursor = decode_adjustment_cursor(body["meta"]["pagination"]["next_cursor"],
        tenant_id=c.tenant, user_id=c.user, project_id=c.project)
    assert cursor.after_id == UUID(int=20)
    c.adjustment_repo.list_adjustments.assert_awaited_once_with(
        tenant_id=c.tenant, project_id=c.project, limit=21, after_id=None)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert snapshot(c) == before and c.session.commits == commits
    c.events.create_event.assert_not_awaited()
    c.serialization.lock_member.assert_not_awaited()
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(tenant_id=c.tenant, module_code="construction.core")


@pytest.mark.asyncio
async def test_max_page_continuation_has_no_duplicates_or_other_project_rows(adjustment_http):
    c = adjustment_http
    first = await call(c, params={"limit": 100})
    assert first.status_code == 200, first.text
    cursor = first.json()["meta"]["pagination"]["next_cursor"]
    second = await call(c, params={"limit": 100, "cursor": cursor})
    assert second.status_code == 200, second.text
    data = first.json()["data"] + second.json()["data"]
    assert [UUID(row["id"]).int for row in data] == list(range(1, 103))
    assert len(second.json()["data"]) == 2
    assert second.json()["meta"] == {"pagination": {"next_cursor": None}}
    assert all(row["project_id"] == str(c.project) and row["tenant_id"] == str(c.tenant) for row in data)
    empty = await call(c, project=c.empty_project)
    assert empty.status_code == 200 and empty.json()["data"] == []
    assert empty.json()["meta"]["pagination"]["next_cursor"] is None


@pytest.mark.asyncio
async def test_detail_includes_disabled_configuration_and_exact_long_decimal(adjustment_http):
    c = adjustment_http
    value = Decimal("123.123456789012345678901234567890123456789")
    c.sync.execute(update(c.adjustments).where(c.adjustments.c.id == UUID(int=2)).values(coefficient=value))
    c.sync.commit()
    with localcontext() as ctx:
        ctx.prec = 2
        response = await call(c, adjustment=UUID(int=2))
    assert response.status_code == 200, response.text
    body, data = response.json(), response.json()["data"]
    assert body["meta"] == {} and set(data) == set(routes.ProjectPricingAdjustmentReadData.model_fields)
    assert data["id"] == str(UUID(int=2)) and data["project_id"] == str(c.project)
    assert data["tenant_id"] == str(c.tenant) and data["created_by"] == str(c.user)
    assert data["coefficient"] == str(value) and data["enabled"] is False
    assert data["created_at"].endswith("Z") and data["updated_at"].endswith("Z")


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [False, True])
async def test_missing_foreign_and_archived_parent_are_identical_safe_not_found(adjustment_http, detail):
    c = adjustment_http
    child = c.adjustment.id if detail else None
    responses = [await call(c, project=c.foreign_project, adjustment=child),
                 await call(c, project=uuid4(), adjustment=child)]
    c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(deleted_at=c.now))
    c.sync.commit()
    responses.append(await call(c, adjustment=child))
    assert all(r.status_code == 404 for r in responses), [r.text for r in responses]
    assert all(r.json() == responses[0].json() for r in responses)
    assert str(c.foreign) not in responses[0].text


@pytest.mark.asyncio
async def test_wrong_project_foreign_and_missing_child_are_hidden(adjustment_http):
    c = adjustment_http
    responses = [await call(c, adjustment=child) for child in (UUID(int=201), UUID(int=301), uuid4())]
    assert all(r.status_code == 404 for r in responses), [r.text for r in responses]
    assert all(r.json() == responses[0].json() for r in responses)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "cancelled"])
async def test_terminal_project_configuration_remains_readable(adjustment_http, status):
    c = adjustment_http
    c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(status=status))
    c.sync.commit()
    assert (await call(c)).status_code == 200
    assert (await call(c, adjustment=c.adjustment.id)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "PROJECT_MANAGER", "ESTIMATOR", "OBSERVER"])
async def test_existing_roles_keep_exact_project_read_scope(adjustment_http, role):
    c = adjustment_http
    c.grant.role = role
    if role not in {"OWNER", "ADMIN"}:
        c.grant.scope_type, c.grant.scope_id = "project", c.project
    response = await call(c)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired", "executor", "other_project"])
async def test_cursor_does_not_bypass_fresh_membership_or_scope(adjustment_http, failure):
    c = adjustment_http
    cursor = encode_adjustment_cursor(Cursor(c.tenant, c.user, c.project, UUID(int=20)),
        tenant_id=c.tenant, user_id=c.user, project_id=c.project)
    if failure == "membership": c.membership = False
    elif failure == "revoked": c.grant.status = "revoked"
    elif failure == "expired": c.grant.expires_at = c.now
    else:
        c.grant.role = "EXECUTOR" if failure == "executor" else "OBSERVER"
        c.grant.scope_type, c.grant.scope_id = "project", c.other_project if failure == "other_project" else c.project
    response = await call(c, params={"cursor": cursor})
    assert response.status_code == 403, response.text
    c.adjustment_repo.list_adjustments.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate"])
async def test_shared_gates_run_before_business_queries(adjustment_http, gate):
    c = adjustment_http
    status, code = 403, "MODULE_DISABLED"
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(status_code=401, code="authentication_required", message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        status, code = 401, "authentication_required"
    elif gate == "suite": c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core": c.core.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        status, code = 429, "RATE_LIMIT_EXCEEDED"
    response = await call(c)
    assert response.status_code == status and response.json()["error"]["code"] == code, response.text
    c.adjustment_repo.get_project.assert_not_awaited()
    c.adjustment_repo.list_adjustments.assert_not_awaited()
    c.adjustment_repo.get_adjustment.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "?limit=0", "?limit=101", "?limit=1.5", "?cursor=", "?cursor=bad!",
    "?cursor=" + "a" * 1025, "?tenant_id=foreign", "?enabled=true", "?limit=1&limit=2"])
async def test_invalid_or_unapproved_list_queries_are_422_before_query(adjustment_http, query):
    c = adjustment_http
    response = await call(c, query=query)
    assert response.status_code == 422, response.text
    c.adjustment_repo.list_adjustments.assert_not_awaited()


@pytest.mark.asyncio
async def test_cursor_is_bound_to_actor_tenant_and_project_and_detail_has_no_selectors(adjustment_http):
    c = adjustment_http
    for tenant, user, project in ((c.foreign, c.user, c.project),
                                  (c.tenant, uuid4(), c.project),
                                  (c.tenant, c.user, c.other_project)):
        value = encode_adjustment_cursor(Cursor(tenant, user, project, UUID(int=20)),
            tenant_id=tenant, user_id=user, project_id=project)
        response = await call(c, params={"cursor": value})
        assert response.status_code == 422, response.text
    response = await call(c, adjustment=c.adjustment.id, params={"tenant_id": str(c.tenant)})
    assert response.status_code == 422, response.text
    c.adjustment_repo.get_adjustment.assert_not_awaited()
    c.adjustment_repo.list_adjustments.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["project", "adjustment"])
async def test_invalid_path_uuid_is_422(adjustment_http, part):
    values = {"adjustment": adjustment_http.adjustment.id, part: "not-a-uuid"}
    response = await call(adjustment_http, **values)
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["project", "list", "adjustment"])
@pytest.mark.parametrize("failure", ["membership", "grant", "archive"])
async def test_access_and_live_parent_are_rechecked_after_awaits(adjustment_http, phase, failure):
    c = adjustment_http
    name = {"project": "get_project", "list": "list_adjustments", "adjustment": "get_adjustment"}[phase]
    original = getattr(c.adjustment_repo, name)
    async def changed(**values):
        row = await original(**values)
        if failure == "membership": c.membership = False
        elif failure == "grant": c.grant.status = "revoked"
        else:
            c.sync.execute(update(c.projects).where(c.projects.c.id == c.project).values(deleted_at=c.now))
        return row
    setattr(c.adjustment_repo, name, changed)
    response = await call(c, adjustment=c.adjustment.id if phase == "adjustment" else None)
    assert response.status_code == (404 if failure == "archive" else 403), response.text
    assert "coefficient" not in response.text
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "tenant", "project", "id", "version", "time", "float", "nan", "name"])
async def test_bad_detail_projection_is_sanitized(adjustment_http, failure):
    c = adjustment_http
    if failure == "exception":
        c.adjustment_service.get_adjustment = AsyncMock(side_effect=RuntimeError("PRIVATE_ADJUSTMENT_DETAILS"))
    else:
        changes = {
            "tenant": {"tenant_id": c.foreign}, "project": {"project_id": c.other_project},
            "id": {"id": uuid4()}, "version": {"row_version": True},
            "time": {"updated_at": c.now.replace(tzinfo=None)}, "float": {"coefficient": 1.1},
            "nan": {"coefficient": Decimal("NaN")}, "name": {"name": " untrimmed "}}[failure]
        c.adjustment_service.get_adjustment = AsyncMock(return_value=replace(c.adjustment, **changes))
    response = await call(c, adjustment=c.adjustment.id)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert "PRIVATE_ADJUSTMENT_DETAILS" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["duplicate", "foreign_item", "wrong_cursor", "oversized"])
async def test_bad_page_never_returns_partial_data(adjustment_http, failure):
    c = adjustment_http
    a = c.adjustment
    if failure == "duplicate": result = Page((a, a), None)
    elif failure == "foreign_item": result = Page((a, replace(a, id=UUID(int=2), tenant_id=c.foreign)), None)
    elif failure == "oversized": result = Page(tuple(replace(a, id=UUID(int=i)) for i in range(1, 22)), None)
    else:
        result = Page(tuple(replace(a, id=UUID(int=i)) for i in range(1, 21)),
                      Cursor(c.foreign, c.user, c.project, UUID(int=20)))
    c.adjustment_service.list_adjustments = AsyncMock(return_value=result)
    response = await call(c)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error" and "data" not in response.json()


@pytest.mark.parametrize("failure", ["duplicate", "boolean_version", "extra", "noncanonical_uuid", "wrong_kind"])
def test_cursor_record_requires_closed_canonical_context(failure):
    tenant, user, project = uuid4(), uuid4(), uuid4()
    record = dict(v=1, kind=KIND, tenant_id=str(tenant), user_id=str(user), project_id=str(project), id=str(uuid4()))
    if failure == "boolean_version": record["v"] = True
    elif failure == "extra": record["extra"] = 1
    elif failure == "noncanonical_uuid": record["project_id"] = project.hex
    elif failure == "wrong_kind": record["kind"] = "other.list"
    raw = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if failure == "duplicate": raw = raw[:-1] + ',"v":1}'
    value = base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii").rstrip("=")
    with pytest.raises(ValidationError):
        decode_adjustment_cursor(value, tenant_id=tenant, user_id=user, project_id=project)


@pytest.mark.asyncio
async def test_registered_openapi_has_exact_version_decimal_string_and_two_read_operations(adjustment_http):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=adjustment_http.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    doc = response.json()
    path = PATH + "{project_id}/pricing-adjustments"
    for suffix in ("", "/{adjustment_id}"):
        op = doc["paths"][path + suffix]["get"]
        assert op["security"] and "requestBody" not in op
        assert {401, 403, 404, 422, 429, 500} <= {int(key) for key in op["responses"]}
        assert not any(p["name"].lower() == "idempotency-key" for p in op.get("parameters", []))
    parameters = {p["name"]: p for p in doc["paths"][path]["get"]["parameters"]}
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["maximum"] == 100
    schema = next(s for s in doc["components"]["schemas"].values()
                  if s.get("title") == "ProjectPricingAdjustmentReadData")
    version = schema["properties"]["row_version"]
    assert type(version["maximum"]) is int and version["maximum"] == 2**63 - 1
    assert version["minimum"] == 1 and version["format"] == "int64"
    assert schema["properties"]["coefficient"]["type"] == "string"
    assert schema["properties"]["enabled"]["type"] == "boolean"


def test_read_factory_passes_the_same_session(monkeypatch):
    session, service = object(), object()
    seen = []
    def factory(**values):
        seen.append(values)
        return service
    monkeypatch.setattr(routes, "build_construction_project_pricing_adjustment_service", factory)
    assert routes.get_adjustment_read_service(session) is service
    assert seen == [{"session": session}]
