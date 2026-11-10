"""Organization template GETs with local services; no external operations."""
import base64
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import update

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.errors import ApiHttpError
from api.routes import construction_organization_templates as routes
from api.routes.construction import (
    get_construction_access_context, get_construction_scoped_session,
)
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
    ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_organization_templates import (
    OrganizationTemplateCursor as Cursor,
    ConstructionOrganizationTemplateValidationError as Validation,
)
from test_construction_02_organization_template_read import (
    case, onboarding, catalog_case, copy_case, read_case, add_version,
)
from test_construction_02_template_copy import copy as create_copy, snapshot

PATH = "/api/v1/construction/settings/measurement-templates"


@pytest.fixture
async def template_http(read_case):
    c = read_case
    c.app = create_app()
    c.suite = NS(require_enabled=AsyncMock())
    c.core = NS(require_enabled=AsyncMock())
    c.rates = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    c.rates.ensure_authenticated_user_allowed = AsyncMock(
        wraps=c.rates.ensure_authenticated_user_allowed)

    async def session():
        if not c.sync.in_transaction():
            c.sync.begin()
        yield c.session

    async def context(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return await c.copier.authorizer.resolver.resolve(actor=c.actor)

    async def service(scoped=Depends(get_construction_scoped_session)):
        assert scoped is c.session
        return c.reader

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_organization_template_service] = service
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


async def get(c, suffix="", *, path=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.get(
            (PATH + suffix) if path is None else path,
            headers={"X-Request-ID": "organization-template-http"})


def version_path(copied):
    return "/versions/" + str(copied.template_version_id)


@pytest.mark.asyncio
async def test_empty_list_uses_shared_gates_without_seeding_or_writes(template_http):
    c = template_http
    before = snapshot(c)
    commits = c.session.commits
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "data": [], "meta": {"pagination": {"next_cursor": None}},
        "request_id": "organization-template-http",
    }
    assert snapshot(c) == before and c.session.commits == commits
    c.events.create_event.assert_not_awaited()
    c.rates.ensure_authenticated_user_allowed.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user)
    c.suite.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, suite_code="construction")
    c.core.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, module_code="construction.core")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_list_and_exact_definition_have_local_references_and_no_new_audit(template_http):
    c = template_http
    copied = await create_copy(c)
    before = snapshot(c)
    commits = c.session.commits
    listed = await get(c)
    assert listed.status_code == 200, listed.text
    summary = listed.json()["data"][0]
    assert summary["tenant_id"] == str(c.tenant)
    assert summary["template_id"] == str(copied.template_id)
    assert summary["version_id"] == str(copied.template_version_id)
    assert summary["source_starter_version_id"] == str(copied.source_starter_version_id)
    assert "definition" not in summary and "section_references" not in summary

    response = await get(c, version_path(copied))
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert {key: body[key] for key in summary} == summary
    stored = await c.copy_repo.get_copy(
        tenant_id=c.tenant, template_id=copied.template_id,
        version_id=copied.template_version_id)
    assert {ref["id"] for ref in body["section_references"]} == {
        str(row["id"]) for row in stored["sections"]}
    assert {q["id"] for ref in body["section_references"] for q in ref["questions"]} == {
        str(row["id"]) for row in stored["questions"]}
    for ref, section in zip(body["section_references"], body["definition"]["sections"]):
        assert ref["code"] == section["code"]
        assert [q["code"] for q in ref["questions"]] == [
            q["code"] for q in section["questions"]]
    expected = await c.reader.get_version(
        actor=c.actor, version_id=copied.template_version_id)
    assert body["definition"] == expected.definition.payload()
    assert snapshot(c) == before and c.session.commits == commits
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_root_and_source_do_not_hide_published_copy(template_http):
    c = template_http
    copied = await create_copy(c)
    c.sync.execute(update(c.copy_repo.templates).where(
        c.copy_repo.templates.c.id == copied.template_id
    ).values(active=False, row_version=2))
    c.sync.execute(update(c.catalog_templates).where(
        c.catalog_templates.c.id == UUID(int=101)).values(active=False))
    c.sync.commit()
    before = snapshot(c)
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["active"] is False
    assert response.json()["data"][0]["template_row_version"] == 2
    assert (await get(c, version_path(copied))).status_code == 200
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_pagination_traverses_two_distinct_roots_without_definition_payload(template_http):
    c = template_http
    a = await create_copy(c, code="COPY_A")
    b = await create_copy(c, code="COPY_B")
    before = snapshot(c)
    first = await get(c, "?limit=1")
    assert first.status_code == 200, first.text
    cursor = first.json()["meta"]["pagination"]["next_cursor"]
    assert isinstance(cursor, str) and len(cursor) == 68
    second = await get(c, "?limit=1&cursor=" + cursor)
    assert second.status_code == 200, second.text
    assert second.json()["meta"]["pagination"]["next_cursor"] is None
    ids = [
        first.json()["data"][0]["template_id"],
        second.json()["data"][0]["template_id"],
    ]
    assert ids == sorted([str(a.template_id), str(b.template_id)])
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_latest_visible_publication_ignores_draft_and_future_version(template_http):
    c = template_http
    copied = await create_copy(c)
    c.source_id = copied.source_starter_version_id
    draft = await add_version(c, copied, number=2, status="DRAFT")
    future = await add_version(c, copied, number=3, future=True)
    response = await get(c)
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["version_id"] == str(copied.template_version_id)
    missing = await get(c, "/versions/" + str(uuid4()))
    for identity in (draft, future):
        hidden = await get(c, "/versions/" + str(identity))
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json()["error"] == missing.json()["error"]


@pytest.mark.asyncio
async def test_organization_binding_is_preserved_in_typed_definition(template_http):
    c = template_http
    copied = await create_copy(c)
    version = await add_version(c, copied, number=2, org=True)
    response = await get(c, "/versions/" + str(version))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    question = data["definition"]["sections"][0]["questions"][1]
    assert question["work_type_source"] == "ORGANIZATION"
    assert question["work_type_version_policy"] == "PINNED"
    assert UUID(question["work_type_id"])
    assert UUID(question["pinned_work_type_version_id"])
    assert data["source_starter_version_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "?tenant_id=foreign", "?user_id=foreign", "?active=true",
    "?limit=0", "?limit=101", "?limit=no",
    "?limit=1&limit=2", "?cursor=", "?cursor=bad",
    "?cursor=" + "A" * 69, "?cursor=a&cursor=b",
])
async def test_list_rejects_invalid_or_ambiguous_selectors(template_http, query):
    c = template_http
    before = snapshot(c)
    response = await get(c, query)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [
    "/versions/not-a-uuid",
    "/versions/00000000-0000-0000-0000-000000000001?tenant_id=foreign",
    "/versions/00000000-0000-0000-0000-000000000001?limit=1",
])
async def test_exact_version_has_no_caller_context_selectors(template_http, suffix):
    c = template_http
    response = await get(c, suffix)
    assert response.status_code == 422, response.text
    c.events.create_event.assert_not_awaited()


@pytest.mark.parametrize("change", ["tenant", "actor", "starter", "invalid", "length"])
def test_cursor_is_resource_actor_and_tenant_bound(change):
    actor = NS(tenant_id=uuid4(), user_id=uuid4())
    cursor = Cursor(actor.tenant_id, actor.user_id, uuid4())
    encoded = routes.encode_cursor(cursor, actor=actor)
    assert routes.decode_cursor(encoded, actor=actor) == cursor
    if change == "tenant":
        actor = NS(tenant_id=uuid4(), user_id=actor.user_id)
    elif change == "actor":
        actor = NS(tenant_id=actor.tenant_id, user_id=uuid4())
    elif change == "starter":
        raw = base64.urlsafe_b64decode(encoded)
        encoded = base64.urlsafe_b64encode(b"ST\x01" + raw[3:]).decode("ascii")
    elif change == "invalid":
        encoded = "!" + encoded[1:]
    else:
        encoded += "A"
    with pytest.raises(Validation):
        routes.decode_cursor(encoded, actor=actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "authentication", "suite", "core", "rate", "membership", "scope", "revoked",
])
async def test_shared_and_fresh_gates_remain_required(template_http, gate):
    c = template_http
    before = snapshot(c)
    expected = 403
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(
                status_code=401, code="authentication_required",
                message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        expected = 401
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.core.require_enabled.side_effect = ModuleDisabledError()
    elif gate == "rate":
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected = 429
    elif gate == "membership":
        c.membership = False
    elif gate == "scope":
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    else:
        c.grant.status = "revoked"
    response = await get(c)
    assert response.status_code == expected, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_authority_is_rechecked_after_content_query(template_http):
    c = template_http
    copied = await create_copy(c)
    original = c.read_repo.get_content

    async def revoke(**kwargs):
        result = await original(**kwargs)
        c.membership = False
        return result

    c.read_repo.get_content = revoke
    before = snapshot(c)
    response = await get(c, version_path(copied))
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.events.create_event.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["private", "foreign", "reference"])
async def test_invalid_backend_results_are_sanitized(template_http, failure):
    c = template_http
    copied = await create_copy(c)
    if not c.sync.in_transaction():
        c.sync.begin()
    state = await c.reader.get_version(
        actor=c.actor, version_id=copied.template_version_id)
    if failure == "private":
        c.reader.get_version = AsyncMock(side_effect=RuntimeError("private-database-detail"))
    elif failure == "foreign":
        c.reader.get_version = AsyncMock(return_value=replace(state, tenant_id=c.foreign))
    else:
        bad = replace(state.sections[0], code="DIFFERENT_SECTION")
        c.reader.get_version = AsyncMock(return_value=replace(
            state, sections=(bad, *state.sections[1:])))
    before = snapshot(c)
    response = await get(c, version_path(copied))
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert "private-database-detail" not in response.text
    assert str(c.foreign) not in response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_reads_need_no_replay_encryption_configuration(template_http, monkeypatch):
    c = template_http
    monkeypatch.delenv("API_IDEMPOTENCY_ENCRYPTION_KEY", raising=False)
    copied = await create_copy(c)
    assert (await get(c)).status_code == 200
    assert (await get(c, version_path(copied))).status_code == 200


@pytest.mark.asyncio
async def test_wire_openapi_has_exact_counter_bounds_and_no_get_key_requirement(template_http):
    response = await get(template_http, path="/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    operation = document["paths"][PATH]["get"]
    parameters = {value["name"]: value for value in operation.get("parameters", [])}
    assert "Idempotency-Key" not in parameters
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["maximum"] == 100
    detail = document["paths"][PATH + "/versions/{version_id}"]["get"]
    assert not any(
        p["name"] == "Idempotency-Key" for p in detail.get("parameters", []))
    assert "post" in document["paths"][PATH + "/copy-starter"]
    schemas = document["components"]["schemas"]
    targets = {
        "OrganizationTemplateSummaryData": ("template_row_version", "version_row_version"),
        "OrganizationTemplateVersionData": ("template_row_version", "version_row_version"),
        "OrganizationTemplateSectionReferenceData": ("row_version",),
        "OrganizationTemplateQuestionReferenceData": ("row_version",),
    }
    for title, fields in targets.items():
        schema = next(item for item in schemas.values() if item.get("title") == title)
        for name in fields:
            counter = schema["properties"][name]
            assert type(counter["maximum"]) is int
            assert counter["maximum"] == 2**63 - 1
            assert counter["minimum"] == 1 and counter["format"] == "int64"
