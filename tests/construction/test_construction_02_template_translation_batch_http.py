"""Registered batch GETs with local SQL and a fake provider only."""
from dataclasses import replace
from urllib.parse import urlencode
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api.auth import get_current_actor
from api.errors import ApiHttpError
from api.routes import construction_organization_templates as organization
from api.routes import construction_starter_templates as starter
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_template_translation_batch import (
    ConstructionTemplateTranslationBatchWorkflow,
)
from services.construction.construction_template_translation_source import (
    template_label_field,
)
from test_construction_02_starter_translation import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, translation_http, starter_http,
    get, cache_rows, domain_snapshot,
)

QUERY = [
    ("fields", "title"), ("fields", "description"),
    ("label_keys", "yes"), ("label_keys", "area"),
]
FIELDS = (
    "title", "description",
    template_label_field("yes"), template_label_field("area"),
)
ORIGINALS = ("Starter", "Structure without prices", "Yes", "Measured area")


@pytest.fixture(params=["organization", "starter"])
async def batch_http(request, starter_http):
    c = starter_http
    c.batch_kind = request.param
    if request.param == "organization":
        c.batch_version = c.base.template_version_id
        c.batch_base = c.version_path
        c.batch_dependency = organization.get_template_translation_batch_workflow
        c.batch_base_dependency = organization.get_template_translation_workflow
        base = c.translation_workflow
    else:
        c.batch_version = c.starter_version
        c.batch_base = c.starter_path
        c.batch_dependency = starter.get_starter_translation_batch_workflow
        c.batch_base_dependency = starter.get_starter_translation_workflow
        base = c.starter_translation

    c.batch_path = c.batch_base + "/translated-texts"
    c.batch = ConstructionTemplateTranslationBatchWorkflow(workflow=base)
    c.batch_calls = AsyncMock(wraps=c.batch.read_for_actor)
    c.batch.read_for_actor = c.batch_calls
    c.app.dependency_overrides[c.batch_dependency] = lambda: c.batch
    yield c


async def request_batch(c, query=QUERY, *, content=None, headers=None, path=None):
    selected = c.batch_path if path is None else path
    encoded = urlencode(query)
    return await get(
        c, path=selected + ("?" + encoded if encoded else ""),
        content=content, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_registered_batch_uses_one_commit_and_reuses_all_cache_rows(batch_http, role):
    c = batch_http
    c.grant.role = role
    before, commits = domain_snapshot(c), c.session.commits
    response = await request_batch(c, headers={"Accept-Language": "en"})
    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert body["request_id"] == "template-translation-http"
    assert body["meta"] == {}
    assert tuple(item["field_name"] for item in data) == FIELDS
    assert tuple(item["original_text"] for item in data) == ORIGINALS
    assert all(item["version_id"] == str(c.batch_version) for item in data)
    assert all(item["tenant_id"] == str(c.tenant) for item in data)
    assert all(item["user_id"] == str(c.user) for item in data)
    assert all(item["target_language"] == "uk" for item in data)
    assert all(item["source_language"] == "en" for item in data)
    assert all(item["source_version"] == 1 for item in data)
    assert all(item["origin"] == "GENERATED" for item in data)
    assert all(item["translation_status"] == "AUTO_TRANSLATED" for item in data)
    assert all(item["cache_written"] for item in data)
    assert "label_key" not in data[0] and "label_key" not in data[1]
    assert [item["label_key"] for item in data[2:]] == ["yes", "area"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert c.session.commits == commits + 1
    assert len(cache_rows(c)) == 4 and domain_snapshot(c) == before
    c.batch_calls.assert_awaited_once_with(
        actor=c.actor, version_id=c.batch_version, field_names=FIELDS)
    c.rates.ensure_authenticated_user_allowed.assert_awaited()

    saved = cache_rows(c)
    again = await request_batch(c)
    assert again.status_code == 200, again.text
    assert all(item["origin"] == "CACHE" for item in again.json()["data"])
    assert all(not item["cache_written"] for item in again.json()["data"])
    assert cache_rows(c) == saved and c.session.commits == commits + 1
    assert c.provider.translate.await_count == 4
    assert domain_snapshot(c) == before


@pytest.mark.asyncio
async def test_labels_only_keep_requested_key_order(batch_http):
    c = batch_http
    response = await request_batch(
        c, [("label_keys", "area"), ("label_keys", "yes")])
    assert response.status_code == 200, response.text
    assert [item["label_key"] for item in response.json()["data"]] == ["area", "yes"]
    assert [item["original_text"] for item in response.json()["data"]] == [
        "Measured area", "Yes"]


@pytest.mark.asyncio
async def test_original_language_is_read_only(batch_http):
    c = batch_http
    c.language = "en"
    commits = c.session.commits
    response = await request_batch(c)
    assert response.status_code == 200, response.text
    assert tuple(item["display_text"] for item in response.json()["data"]) == ORIGINALS
    assert all(item["origin"] == "ORIGINAL" for item in response.json()["data"])
    assert all(item["translation_status"] is None for item in response.json()["data"])
    assert cache_rows(c) == () and c.session.commits == commits
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    [],
    [("fields", "title"), ("fields", "title")],
    [("label_keys", "yes"), ("label_keys", "yes")],
    [("fields", "phone")],
    [("label_keys", "bad/key")],
    [("fields", "title"), ("language", "en")],
    [("fields", "title"), ("tenant_id", "foreign")],
    [("fields", "title"), ("source_text", "caller text")],
    [("fields", "title"), ("source_version", "99")],
    [("fields", "title"), ("fields", "description")]
        + [("label_keys", "key" + str(i)) for i in range(31)],
])
async def test_invalid_selection_never_reaches_batch_workflow(batch_http, query):
    c = batch_http
    response = await request_batch(c, query)
    assert response.status_code == 422, response.text
    c.batch_calls.assert_not_awaited()
    c.provider.translate.assert_not_awaited()
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_body_cannot_supply_source_data(batch_http):
    c = batch_http
    response = await request_batch(
        c, content='{"source_text":"caller text"}',
        headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    c.batch_calls.assert_not_awaited()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "authentication", "membership", "scope", "suite", "core", "rate",
])
async def test_existing_http_and_domain_boundaries_remain_required(batch_http, gate):
    c = batch_http
    expected = 403
    if gate == "authentication":
        async def denied():
            raise ApiHttpError(
                status_code=401, code="authentication_required",
                message="Authentication required.")
        c.app.dependency_overrides[get_current_actor] = denied
        expected = 401
    elif gate == "membership":
        c.membership = False
    elif gate == "scope":
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    elif gate == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif gate == "core":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rates.ensure_authenticated_user_allowed.side_effect = (
            ApiRequestRateLimitExceededError())
        expected = 429
    response = await request_batch(c)
    assert response.status_code == expected, response.text
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["version", "label"])
async def test_unavailable_source_returns_safe_not_found_without_partial_cache(
    batch_http, missing,
):
    c = batch_http
    path = None
    query = QUERY
    if missing == "version":
        prefix = c.batch_base.rsplit("/", 1)[0]
        path = prefix + "/" + str(uuid4()) + "/translated-texts"
    else:
        query = [("fields", "title"), ("label_keys", "missing")]
    response = await request_batch(c, query, path=path)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "second_insert", "commit"])
async def test_real_local_batch_failure_rolls_back_and_is_sanitized(batch_http, failure):
    c = batch_http
    inserts = []
    original = c.translation_repo.insert_automatic
    if failure == "provider":
        c.provider.translate.side_effect = RuntimeError("private provider credentials")
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        async def failing(**values):
            result = await original(**values)
            inserts.append(result)
            if len(inserts) == 2:
                raise RuntimeError("private failure after two local inserts")
            return result
        c.translation_repo.insert_automatic = failing
    before, commits = domain_snapshot(c), c.session.commits
    response = await request_batch(c)
    assert response.status_code == (503 if failure == "provider" else 500), response.text
    assert "private" not in response.text
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits
    if failure == "second_insert":
        assert len(inserts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["language", "membership"])
async def test_fresh_context_after_provider_is_required(batch_http, change):
    c = batch_http
    async def changed(**values):
        if change == "language":
            c.language = "it"
        else:
            c.membership = False
        return "Translated"
    c.provider.translate.side_effect = changed
    response = await request_batch(c)
    assert response.status_code == (409 if change == "language" else 403), response.text
    if change == "language":
        assert response.json()["error"]["code"] == "LANGUAGE_CONTEXT_CHANGED"
    assert cache_rows(c) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["actor", "order", "language", "length"])
async def test_public_projection_rejects_mismatched_results(batch_http, wrong):
    c = batch_http
    if not c.sync.in_transaction():
        c.sync.begin()
    good = await c.batch.read_for_actor(
        actor=c.actor, version_id=c.batch_version, field_names=FIELDS)
    altered = list(good)
    if wrong == "actor":
        altered[0] = replace(altered[0], user_id=uuid4())
    elif wrong == "order":
        altered[0], altered[1] = altered[1], altered[0]
    elif wrong == "language":
        altered[0] = replace(altered[0], target_language="it")
    else:
        altered.pop()
    c.batch.read_for_actor = AsyncMock(return_value=tuple(altered))
    before, commits = cache_rows(c), c.session.commits
    response = await request_batch(c)
    assert response.status_code == 500, response.text
    assert cache_rows(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_registered_factory_wraps_the_existing_shared_workflow(batch_http):
    c = batch_http
    c.app.dependency_overrides.pop(c.batch_dependency)
    response = await request_batch(c)
    assert response.status_code == 200, response.text
    assert len(cache_rows(c)) == 4


@pytest.mark.asyncio
async def test_invalid_runtime_collaborator_is_sanitized_as_unavailable(batch_http):
    c = batch_http
    c.app.dependency_overrides.pop(c.batch_dependency)
    c.app.dependency_overrides[c.batch_base_dependency] = lambda: object()
    response = await request_batch(c)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "TRANSLATION_UNAVAILABLE"
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_single_text_and_source_version_reads_are_preserved(batch_http):
    c = batch_http
    translated = await get(c, path=c.batch_base + "/texts/title")
    assert translated.status_code == 200, translated.text
    assert translated.json()["data"]["original_text"] == "Starter"
    original = await get(c, path=c.batch_base)
    assert original.status_code == 200, original.text
    assert original.json()["data"]["source_language"] == "en"
    assert original.json()["data"]["title"] == "Starter"


@pytest.mark.asyncio
async def test_openapi_describes_only_bounded_read_selectors(batch_http):
    c = batch_http
    response = await get(c, path="/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    prefix = (
        "/api/v1/construction/settings/measurement-templates"
        if c.batch_kind == "organization" else
        "/api/v1/construction/settings/starter-measurement-templates")
    path = prefix + "/versions/{version_id}/translated-texts"
    operation = document["paths"][path]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {"version_id", "fields", "label_keys"}
    assert "requestBody" not in operation
    assert {"409", "422", "429", "503"}.issubset(operation["responses"])

    schemas = document["components"]["schemas"]
    envelope = next(
        item for item in schemas.values()
        if item.get("title") == "TemplateTranslatedBatchEnvelope")
    data = envelope["properties"]["data"]
    assert data["minItems"] == 1 and data["maxItems"] == 32
    assert len(data["items"]["anyOf"]) == 2
    for title in ("TemplateTranslatedTextData", "TemplateTranslatedLabelData"):
        schema = next(item for item in schemas.values() if item.get("title") == title)
        for name in ("source_version", "cache_row_version"):
            integer = next(
                branch for branch in schema["properties"][name]["anyOf"]
                if branch.get("type") == "integer")
            assert type(integer["maximum"]) is int
            assert integer["maximum"] == 2**63 - 1
            assert integer["format"] == "int64"
