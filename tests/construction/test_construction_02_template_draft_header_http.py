"""Registered sparse draft-header PATCH with local encrypted workflows."""
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Depends

from api.auth import get_current_actor
from api.errors import ApiHttpError
from api.routes import construction_template_versions as routes
from api.routes.construction import get_construction_scoped_session
from services.construction.construction_template_draft_header_workflow import (
    ConstructionTemplateDraftHeaderWorkflow,
)
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from api.settings import ApiConfigurationError

from test_construction_02_template_versions_http import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, keyed,
    version_http, create, root, call, snapshot,
)


@pytest.fixture
async def header_http(version_http):
    c = version_http
    c.header_workflow = ConstructionTemplateDraftHeaderWorkflow(
        session=c.session, content_service=c.content,
        idempotency_service=c.idempotency)
    original = c.content.edit_header
    c.header_calls = AsyncMock(side_effect=original)
    c.content.edit_header = c.header_calls

    async def workflow(
        scoped=Depends(get_construction_scoped_session),
        key=Depends(routes.require_template_version_key),
    ):
        assert scoped is c.session
        return c.header_workflow

    c.app.dependency_overrides[routes.get_draft_header_workflow] = workflow
    c.created = await create(c)
    c.path = root(c) + "/" + c.created["version_id"]
    yield c


async def current(c):
    response = await call(c, "GET", c.path)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def renamed(state, title="Organization template"):
    labels = deepcopy(state["definition"]["labels"])
    labels[state["source_language"]][state["definition"]["title_key"]] = title
    return {"labels": labels}


async def patch(c, fields, *, expected=1, key="header-http-key", headers=None, path=None):
    return await call(
        c, "PATCH", c.path if path is None else path,
        body={"expected_row_version": expected, **fields},
        key=key, headers=headers)


def test_request_preserves_omission_and_explicit_null():
    assert routes.TemplateDraftHeaderPatchRequest(
        expected_row_version=1).patch_fields() == {}
    request = routes.TemplateDraftHeaderPatchRequest(
        expected_row_version=1, description_key=None)
    assert request.patch_fields() == {"description_key": None}


@pytest.mark.asyncio
async def test_sparse_edit_preserves_other_data_and_replays_original_receipt(header_http):
    c = header_http
    old = await current(c)
    fields = renamed(old)
    before, commits = snapshot(c), c.session.commits
    response = await patch(c, fields)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["changed"] is True and data["version_row_version"] == 2
    assert data["title"] == "Organization template"
    assert data["created_by"] == old["created_by"]
    assert "definition" not in data and "section_references" not in data
    assert c.session.commits == commits + 1
    assert c.header_calls.await_args.kwargs["fields"] == fields
    assert c.header_calls.await_args.kwargs["manage_transaction"] is False

    saved = await current(c)
    assert saved["section_references"] == old["section_references"]
    assert saved["source_language"] == old["source_language"]
    assert saved["definition"]["sections"] == old["definition"]["sections"]
    after = snapshot(c)
    assert after[0][0] == before[0][0]
    assert after[0][2:7] == before[0][2:7]
    replay = await patch(c, fields)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == data
    assert snapshot(c) == after
    c.header_calls.assert_awaited_once()
    event = c.events.create_event.await_args.kwargs
    assert event["trace_id"] == "template-version-http"
    assert event["payload"]["operation"] == "edit_draft_header"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_current_noop_has_receipt_without_version_increment_or_audit(header_http):
    c = header_http
    before = snapshot(c)
    events = c.events.create_event.await_count
    response = await patch(c, {})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["changed"] is False
    assert response.json()["data"]["version_row_version"] == 1
    assert snapshot(c)[0] == before[0]
    assert c.events.create_event.await_count == events
    after = snapshot(c)
    replay = await patch(c, {})
    assert replay.status_code == 200
    assert replay.json()["data"] == response.json()["data"]
    assert snapshot(c) == after
    c.header_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_noop_is_conflict(header_http):
    c = header_http
    state = await current(c)
    assert (await patch(c, renamed(state))).status_code == 200
    before = snapshot(c)
    response = await patch(c, {}, key="stale-noop-key")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_same_key_changed_payload_is_idempotency_conflict(header_http):
    c = header_http
    state = await current(c)
    assert (await patch(c, renamed(state))).status_code == 200
    before = snapshot(c)
    response = await patch(c, {})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_explicit_null_clears_description_with_coherent_labels(header_http):
    c = header_http
    state = await current(c)
    labels = deepcopy(state["definition"]["labels"])
    description_key = state["definition"]["description_key"]
    labels[state["source_language"]].pop(description_key)
    fields = {"description_key": None, "labels": labels}
    response = await patch(c, fields)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["description"] is None
    assert c.header_calls.await_args.kwargs["fields"] == fields
    saved = await current(c)
    assert saved["definition"]["description_key"] is None


@pytest.mark.asyncio
async def test_header_replay_after_publication_keeps_original_write_metadata(header_http):
    c = header_http
    state = await current(c)
    fields = renamed(state)
    first = await patch(c, fields)
    assert first.status_code == 200, first.text
    published = await call(
        c, "POST", c.path + "/publish",
        body={"expected_row_version": 2}, key="later-publish-key")
    assert published.status_code == 200, published.text
    before = snapshot(c)
    replay = await patch(c, fields)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert snapshot(c) == before
    new_request = await patch(c, fields, expected=3, key="new-edit-after-publish")
    assert new_request.status_code == 404, new_request.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [], [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)],
    [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_header_key_is_required_unique_and_bounded(header_http, headers):
    c = header_http
    before = snapshot(c)
    response = await patch(c, {}, headers=headers)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.header_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"tenant_id": str(uuid4())},
    {"user_id": str(uuid4())},
    {"status": "PUBLISHED"},
    {"definition": {}},
    {"sections": []},
    {"title_key": None},
    {"source_language": None},
    {"labels": None},
    {"labels": {"en": {"title": 42}}},
    {"expected_row_version": True},
    {"expected_row_version": "1"},
    {"expected_row_version": 2**63},
])
async def test_closed_sparse_body_and_strict_versions(header_http, fields):
    c = header_http
    before = snapshot(c)
    response = await patch(c, fields)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_incomplete_labels_and_query_selectors_cannot_write(header_http):
    c = header_http
    before = snapshot(c)
    response = await patch(c, {"labels": {"en": {"title": "Incomplete"}}})
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    response = await patch(c, {}, path=c.path + "?tenant_id=foreign")
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "authentication", "suite", "core", "rate", "membership", "scope", "revoked",
])
async def test_existing_security_gates_remain_mandatory(header_http, gate):
    c = header_http
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
    response = await patch(c, {})
    assert response.status_code == expected, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_http_failure_rolls_back_edit_audit_and_receipt(header_http, failure):
    c = header_http
    state = await current(c)
    if failure == "audit":
        c.fail_audit = True
    elif failure == "complete":
        c.fail_complete = "error"
    else:
        c.session.fail_commit = True
    before, commits = snapshot(c), c.session.commits
    response = await patch(c, renamed(state))
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_missing_replay_configuration_is_sanitized(header_http, monkeypatch):
    c = header_http
    c.app.dependency_overrides.pop(routes.get_draft_header_workflow)
    def missing(cls, *args, **kwargs):
        raise ApiConfigurationError("private missing configuration")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(missing))
    before = snapshot(c)
    response = await patch(c, {})
    assert response.status_code == 503, response.text
    assert "private missing configuration" not in response.text
    assert snapshot(c) == before
    assert (await call(c, "GET", c.path)).status_code == 200


@pytest.mark.asyncio
async def test_wire_openapi_describes_sparse_patch_and_exact_counters(header_http):
    c = header_http
    response = await call(c, "GET", "/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    path = (
        "/api/v1/construction/settings/measurement-templates/"
        "{template_id}/drafts/{version_id}")
    operation = document["paths"][path]["patch"]
    keys = [p for p in operation["parameters"] if p["name"] == "Idempotency-Key"]
    assert len(keys) == 1 and keys[0]["required"] is True
    schemas = document["components"]["schemas"]
    body = next(s for s in schemas.values()
                if s.get("title") == "TemplateDraftHeaderPatchRequest")
    assert set(body["required"]) == {"expected_row_version"}
    assert body["additionalProperties"] is False
    targets = {
        "TemplateDraftHeaderPatchRequest": ("expected_row_version",),
        "TemplateDraftWriteData": ("template_row_version", "version_row_version"),
    }
    for title, names in targets.items():
        schema = next(s for s in schemas.values() if s.get("title") == title)
        for name in names:
            field = schema["properties"][name]
            assert type(field["maximum"]) is int
            assert field["maximum"] == 2**63 - 1
            assert field["minimum"] == 1 and field["format"] == "int64"
