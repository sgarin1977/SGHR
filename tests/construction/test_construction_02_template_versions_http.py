"""Registered template version HTTP with local workflows and encrypted receipts."""
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
from api.settings import ApiConfigurationError
from api.routes import construction_template_versions as routes
from api.routes import construction_organization_templates as published_routes
from api.routes.construction import (
    get_construction_access_context, get_construction_scoped_session,
)
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
    ApiRequestRateLimitExceededError,
)
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_template_publication import (
    ConstructionTemplatePublicationService,
)
from services.construction.construction_template_publication_workflow import (
    ConstructionTemplatePublicationWorkflow,
)
from database.repositories.construction.construction_template_publication import (
    ConstructionTemplatePublicationRepository,
)
from test_construction_02_template_draft_workflow import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, keyed,
    local_content, snapshot,
)

PREFIX = "/api/v1/construction/settings/measurement-templates"


@pytest.fixture
async def version_http(keyed):
    c = keyed
    c.content = local_content(c)
    repo = ConstructionTemplatePublicationRepository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repo, name, getattr(c.draft_repo, name))
    c.publisher = ConstructionTemplatePublicationService(
        session=c.session, repository=repo,
        content_service=c.content, published_service=c.reader)
    c.publish_workflow = ConstructionTemplatePublicationWorkflow(
        session=c.session, publication_service=c.publisher,
        idempotency_service=c.idempotency)
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
        return await c.drafts.authorizer.resolver.resolve(actor=c.actor)

    async def draft_workflow(
        scoped=Depends(get_construction_scoped_session),
        key=Depends(routes.require_template_version_key),
    ):
        assert scoped is c.session
        return c.workflow

    async def publication_workflow(
        scoped=Depends(get_construction_scoped_session),
        key=Depends(routes.require_template_version_key),
    ):
        assert scoped is c.session
        return c.publish_workflow

    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_scoped_session] = session
    c.app.dependency_overrides[get_construction_access_context] = context
    c.app.dependency_overrides[routes.get_draft_workflow] = draft_workflow
    c.app.dependency_overrides[routes.get_publication_workflow] = publication_workflow
    c.app.dependency_overrides[routes.get_draft_content_service] = lambda: c.content
    c.app.dependency_overrides[routes.get_publication_service] = lambda: c.publisher
    c.app.dependency_overrides[published_routes.get_organization_template_service] = lambda: c.reader
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rates
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.core
    yield c


def root(c):
    return PREFIX + "/" + str(c.base.template_id) + "/drafts"


async def call(c, method, path, *, body=None, key=None, headers=None):
    request_headers = [("X-Request-ID", "template-version-http")]
    if headers is not None:
        request_headers.extend(headers)
    elif key is not None:
        request_headers.append(("Idempotency-Key", key))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        return await client.request(
            method, path, json=body, headers=request_headers)


async def create(c):
    response = await call(c, "POST", root(c), key="http-draft-key", body={
        "base_version_id": str(c.base.template_version_id),
        "expected_template_row_version": c.base.template_row_version,
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.mark.asyncio
async def test_full_http_lifecycle_and_original_receipt_replays(version_http):
    c = version_http
    created = await create(c)
    path = root(c) + "/" + created["version_id"]
    commits = c.session.commits
    read = await call(c, "GET", path)
    assert read.status_code == 200, read.text
    draft = read.json()["data"]
    assert draft["status"] == "DRAFT"
    assert draft["version_row_version"] == 1
    assert len(draft["section_references"]) == 2
    assert sum(len(s["questions"]) for s in draft["section_references"]) == 3
    ready = await call(c, "POST", path + "/validate", body={"expected_row_version": 1})
    assert ready.status_code == 200, ready.text
    assert ready.json()["data"]["binding_count"] == 0
    assert c.session.commits == commits
    published = await call(
        c, "POST", path + "/publish", key="http-publish-key",
        body={"expected_row_version": 1})
    assert published.status_code == 200, published.text
    metadata = published.json()["data"]
    assert metadata["version_row_version"] == 2
    assert metadata["content_hash"] == ready.json()["data"]["definition_hash"]
    assert metadata["version_id"] == created["version_id"]
    before = snapshot(c)
    assert await create(c) == created
    replay = await call(
        c, "POST", path + "/publish", key="http-publish-key",
        body={"expected_row_version": 1})
    assert replay.status_code == 200 and replay.json()["data"] == metadata
    assert snapshot(c) == before
    assert (await call(c, "GET", path)).status_code == 404
    exact = await call(c, "GET", PREFIX + "/versions/" + created["version_id"])
    assert exact.status_code == 200, exact.text
    assert exact.json()["data"]["definition"] == draft["definition"]
    for response in (read, ready, published, replay, exact):
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["Pragma"] == "no-cache"


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [], [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)],
    [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_creation_key_is_required_unique_and_bounded(version_http, headers):
    c = version_http
    before = snapshot(c)
    response = await call(c, "POST", root(c), headers=headers, body={
        "base_version_id": str(c.base.template_version_id),
        "expected_template_row_version": 1})
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.create_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"tenant_id": str(uuid4())}, {"user_id": str(uuid4())},
    {"status": "PUBLISHED"}, {"base_version_id": "bad"},
    {"expected_template_row_version": "1"},
    {"expected_template_row_version": True},
    {"expected_template_row_version": 0},
])
async def test_creation_body_is_closed_and_versions_are_strict(version_http, change):
    c = version_http
    before = snapshot(c)
    response = await call(c, "POST", root(c), key="invalid-body", body={
        "base_version_id": str(c.base.template_version_id),
        "expected_template_row_version": 1, **change})
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "authentication", "suite", "core", "rate", "membership", "scope", "revoked",
])
async def test_shared_security_gates_are_preserved(version_http, gate):
    c = version_http
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
    before = snapshot(c)
    response = await call(c, "POST", root(c), key="gated", body={
        "base_version_id": str(c.base.template_version_id),
        "expected_template_row_version": 1})
    assert response.status_code == expected, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_read_and_validation_need_no_encryption_configuration(version_http, monkeypatch):
    c = version_http
    created = await create(c)
    path = root(c) + "/" + created["version_id"]
    def missing(cls, *args, **kwargs):
        raise ApiConfigurationError("private missing configuration")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(missing))
    assert (await call(c, "GET", path)).status_code == 200
    assert (await call(
        c, "POST", path + "/validate", body={"expected_row_version": 1}
    )).status_code == 200
    c.app.dependency_overrides.pop(routes.get_publication_workflow)
    before = snapshot(c)
    response = await call(
        c, "POST", path + "/publish", key="configured-key",
        body={"expected_row_version": 1})
    assert response.status_code == 503, response.text
    assert "private missing configuration" not in response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_creation_configuration_failure_is_sanitized(version_http, monkeypatch):
    c = version_http
    c.app.dependency_overrides.pop(routes.get_draft_workflow)
    def missing(cls, *args, **kwargs):
        raise ApiConfigurationError("private missing configuration")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(missing))
    before = snapshot(c)
    response = await call(c, "POST", root(c), key="configured-key", body={
        "base_version_id": str(c.base.template_version_id),
        "expected_template_row_version": 1})
    assert response.status_code == 503, response.text
    assert "private missing configuration" not in response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_query_selectors_and_stale_validation_are_rejected(version_http):
    c = version_http
    created = await create(c)
    path = root(c) + "/" + created["version_id"]
    assert (await call(c, "GET", path + "?tenant_id=foreign")).status_code == 422
    assert (await call(
        c, "POST", path + "/validate?user_id=foreign",
        body={"expected_row_version": 1})).status_code == 422
    before = snapshot(c)
    stale = await call(
        c, "POST", path + "/validate", body={"expected_row_version": 2})
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_missing_and_wrong_template_context_are_safe_not_found(version_http):
    c = version_http
    created = await create(c)
    responses = [
        await call(c, "GET", root(c) + "/" + str(uuid4())),
        await call(
            c, "GET",
            PREFIX + "/" + str(uuid4()) + "/drafts/" + created["version_id"]),
    ]
    assert all(r.status_code == 404 for r in responses)
    assert responses[0].json()["error"] == responses[1].json()["error"]


@pytest.mark.asyncio
async def test_unconfigured_work_binding_validator_blocks_publication(version_http):
    c = version_http
    created = await create(c)
    path = root(c) + "/" + created["version_id"]
    draft = (await call(c, "GET", path)).json()["data"]
    question_id = UUID(draft["section_references"][0]["questions"][1]["id"])
    c.sync.execute(update(c.draft_repo.questions).where(
        c.draft_repo.questions.c.id == question_id
    ).values(
        work_type_source="ORGANIZATION", work_type_id=uuid4(),
        work_type_version_policy="CURRENT_PUBLISHED"))
    c.sync.commit()
    before = snapshot(c)
    for suffix, key in (("/validate", None), ("/publish", "binding-key")):
        response = await call(
            c, "POST", path + suffix, key=key,
            body={"expected_row_version": 1})
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "TEMPLATE_BINDING_UNAVAILABLE"
        assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "publish"])
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_http_failure_rolls_back_domain_audit_and_receipt(version_http, operation, failure):
    c = version_http
    if operation == "publish":
        created = await create(c)
        path = root(c) + "/" + created["version_id"] + "/publish"
        body = {"expected_row_version": 1}
    else:
        path = root(c)
        body = {
            "base_version_id": str(c.base.template_version_id),
            "expected_template_row_version": 1}
    if failure == "audit":
        c.fail_audit = True
    elif failure == "complete":
        c.fail_complete = "error"
    else:
        c.session.fail_commit = True
    before, commits = snapshot(c), c.session.commits
    response = await call(c, "POST", path, key="failure-key", body=body)
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_wire_openapi_has_four_operations_and_exact_bigint_bounds(version_http):
    response = await call(version_http, "GET", "/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    root_path = PREFIX + "/{template_id}/drafts"
    detail = root_path + "/{version_id}"
    paths = document["paths"]
    for path, method, keyed in (
        (root_path, "post", True), (detail, "get", False),
        (detail + "/validate", "post", False),
        (detail + "/publish", "post", True),
    ):
        operation = paths[path][method]
        keys = [p for p in operation.get("parameters", [])
                if p["name"] == "Idempotency-Key"]
        assert bool(keys) is keyed
        if keyed:
            assert keys[0]["required"] is True
    targets = {
        "TemplateDraftCreateRequest": ("expected_template_row_version",),
        "TemplateDraftVersionRequest": ("expected_row_version",),
        "TemplateDraftCreatedData": ("template_row_version",),
        "TemplateDraftData": ("template_row_version", "version_row_version"),
        "TemplatePublicationReadinessData": ("template_row_version", "version_row_version"),
        "TemplatePublicationData": ("template_row_version", "version_row_version"),
    }
    schemas = document["components"]["schemas"]
    for title, names in targets.items():
        schema = next(s for s in schemas.values() if s.get("title") == title)
        for name in names:
            field = schema["properties"][name]
            assert type(field["maximum"]) is int
            assert field["maximum"] == 2**63 - 1
            assert field["minimum"] == 1 and field["format"] == "int64"
