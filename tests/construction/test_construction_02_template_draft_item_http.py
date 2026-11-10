"""Registered item PATCH routes using real local domain workflows."""
from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Depends

from api.auth import get_current_actor
from api.errors import ApiHttpError
from api.settings import ApiConfigurationError
from api.routes import construction_template_versions as routes
from api.routes.construction import get_construction_scoped_session
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemService,
)
from services.construction.construction_template_draft_item_workflow import (
    ConstructionTemplateDraftItemWorkflow,
)
from database.repositories.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemRepository,
)
from test_construction_02_template_versions_http import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, keyed,
    version_http, create, root, call, snapshot,
)


@pytest.fixture
async def item_http(version_http):
    c = version_http
    repository = ConstructionTemplateDraftItemRepository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.draft_repo, name))
    c.items = ConstructionTemplateDraftItemService(
        session=c.session, repository=repository, content_service=c.content)
    c.item_workflow = ConstructionTemplateDraftItemWorkflow(
        session=c.session, item_service=c.items,
        idempotency_service=c.idempotency)
    c.item_calls = AsyncMock(wraps=c.items.edit_item)
    c.items.edit_item = c.item_calls

    async def workflow(
        scoped=Depends(get_construction_scoped_session),
        key=Depends(routes.require_template_version_key),
    ):
        assert scoped is c.session
        return c.item_workflow

    c.app.dependency_overrides[routes.get_draft_item_workflow] = workflow
    c.created = await create(c)
    c.path = root(c) + "/" + c.created["version_id"]
    c.initial = await current(c)
    yield c


async def current(c):
    response = await call(c, "GET", c.path)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def reference(state, kind):
    section = state["section_references"][0]
    return section if kind == "SECTION" else section["questions"][1]


def item_path(c, kind="QUESTION", item_id=None):
    segment = "sections" if kind == "SECTION" else "questions"
    selected = reference(c.initial, kind)["id"] if item_id is None else str(item_id)
    return c.path + "/" + segment + "/" + selected


async def patch(
    c, fields, *, kind="QUESTION", expected=1, key="item-http-key",
    extra=None, headers=None, path=None,
):
    return await call(
        c, "PATCH", item_path(c, kind) if path is None else path,
        body={"expected_row_version": expected, "fields": fields, **(extra or {})},
        key=key, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,fields", [
    ("SECTION", {"sort_order": 5}),
    ("QUESTION", {"required": False}),
])
async def test_edit_preserves_identity_and_replays_without_second_write(item_http, kind, fields):
    c = item_http
    before, commits = snapshot(c), c.session.commits
    old = reference(c.initial, kind)
    response = await patch(c, fields, kind=kind)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["kind"] == kind and data["item_id"] == old["id"]
    assert data["draft"]["changed"] is True
    assert data["draft"]["version_row_version"] == 2
    assert data["draft"]["created_by"] == c.initial["created_by"]
    assert "definition" not in data["draft"]
    assert "section_references" not in data["draft"]
    assert c.session.commits == commits + 1

    kwargs = c.item_calls.await_args.kwargs
    assert kwargs["fields"] == fields and kwargs["kind"] == kind
    assert kwargs["manage_transaction"] is False
    assert "labels" not in kwargs
    saved = await current(c)
    new = reference(saved, kind)
    assert new["id"] == old["id"] and new["row_version"] == old["row_version"] + 1
    assert saved["definition"]["labels"] == c.initial["definition"]["labels"]
    assert snapshot(c)[0][0] == before[0][0]
    after = snapshot(c)
    replay = await patch(c, fields, kind=kind)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == data
    assert snapshot(c) == after
    c.item_calls.assert_awaited_once()
    event = c.events.create_event.await_args.kwargs
    assert event["trace_id"] == "template-version-http"
    assert event["payload"]["operation"] == "edit_draft_" + kind.lower()
    assert event["payload"]["item_id"] == old["id"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_label_reference_and_dictionary_change_atomically(item_http):
    c = item_http
    labels = deepcopy(c.initial["definition"]["labels"])
    language = c.initial["source_language"]
    old_key = c.initial["definition"]["sections"][0]["questions"][1]["label_key"]
    labels[language]["customArea"] = labels[language].pop(old_key)
    response = await patch(
        c, {"label_key": "customArea"}, extra={"labels": labels})
    assert response.status_code == 200, response.text
    saved = await current(c)
    assert saved["definition"]["labels"] == labels
    assert saved["definition"]["sections"][0]["questions"][1]["label_key"] == "customArea"
    assert c.item_calls.await_args.kwargs["labels"] == labels


@pytest.mark.asyncio
async def test_explicit_null_clears_nullable_field_and_preserves_omitted_fields(item_http):
    c = item_http
    first = await patch(c, {"help_media_ref": "areaHelp"})
    assert first.status_code == 200, first.text
    second = await patch(
        c, {"help_media_ref": None}, expected=2, key="clear-help-key")
    assert second.status_code == 200, second.text
    saved = await current(c)
    question = saved["definition"]["sections"][0]["questions"][1]
    original = c.initial["definition"]["sections"][0]["questions"][1]
    assert question["help_media_ref"] is None
    assert question == original
    assert c.item_calls.await_args.kwargs["fields"] == {"help_media_ref": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
async def test_current_noop_has_receipt_without_new_audit_or_counter(item_http, kind):
    c = item_http
    before, events = snapshot(c), c.events.create_event.await_count
    response = await patch(c, {}, kind=kind)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["draft"]["changed"] is False
    assert response.json()["data"]["draft"]["version_row_version"] == 1
    assert snapshot(c)[0] == before[0]
    assert c.events.create_event.await_count == events
    after = snapshot(c)
    replay = await patch(c, {}, kind=kind)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == response.json()["data"]
    assert snapshot(c) == after
    c.item_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_noop_is_409(item_http):
    c = item_http
    assert (await patch(c, {"required": False})).status_code == 200
    before = snapshot(c)
    response = await patch(c, {}, key="stale-item-key")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["fields", "item", "kind", "version"])
async def test_reused_key_binds_payload_and_exact_item_context(item_http, change):
    c = item_http
    assert (await patch(c, {})).status_code == 200
    fields, options = {}, {}
    if change == "fields":
        fields = {"required": False}
    elif change == "item":
        gate = c.initial["section_references"][0]["questions"][0]["id"]
        options["path"] = item_path(c, item_id=gate)
    elif change == "kind":
        options["kind"] = "SECTION"
    else:
        options["expected"] = 2
    before = snapshot(c)
    response = await patch(c, fields, **options)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before
    c.item_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_replay_after_publication_returns_original_draft_receipt(item_http):
    c = item_http
    first = await patch(c, {"required": False})
    assert first.status_code == 200, first.text
    published = await call(
        c, "POST", c.path + "/publish",
        body={"expected_row_version": 2}, key="item-later-publish-key")
    assert published.status_code == 200, published.text
    before = snapshot(c)
    replay = await patch(c, {"required": False})
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert snapshot(c) == before
    denied = await patch(
        c, {"required": True}, expected=3, key="new-item-after-publish")
    assert denied.status_code == 404, denied.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,fields,extra", [
    ("SECTION", {"questions": []}, {}),
    ("SECTION", {"required": False}, {}),
    ("QUESTION", {"id": str(uuid4())}, {}),
    ("QUESTION", {"template_section_id": str(uuid4())}, {}),
    ("QUESTION", {"sort_order": True}, {}),
    ("QUESTION", {"sort_order": -1}, {}),
    ("QUESTION", {"sort_order": 2**31}, {}),
    ("QUESTION", {"required": "false"}, {}),
    ("QUESTION", {"required": None}, {}),
    ("QUESTION", {"validation_schema": {"minimum": 1.25}}, {}),
    ("QUESTION", {"work_type_id": "invalid"}, {}),
    ("QUESTION", {"show_if": {"question_code": "AREA", "operator": "EQ", "value": "YES"}}, {}),
    ("QUESTION", {}, {"labels": None}),
    ("QUESTION", {}, {"labels": {"en": {"area": "Incomplete"}}}),
    ("QUESTION", {}, {"expected_row_version": True}),
    ("QUESTION", {}, {"expected_row_version": "1"}),
    ("QUESTION", {}, {"expected_row_version": 2**63}),
    ("QUESTION", {}, {"tenant_id": str(uuid4())}),
])
async def test_invalid_or_caller_controlled_content_cannot_write(item_http, kind, fields, extra):
    c = item_http
    before = snapshot(c)
    response = await patch(c, fields, kind=kind, extra=extra)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
async def test_missing_item_is_safe_not_found(item_http, kind):
    c = item_http
    before = snapshot(c)
    response = await patch(c, {}, kind=kind, path=item_path(c, kind, uuid4()))
    assert response.status_code == 404, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_foreign_template_and_query_selectors_cannot_write(item_http):
    c = item_http
    before = snapshot(c)
    foreign_path = item_path(c).replace(str(c.base.template_id), str(uuid4()))
    missing = await patch(c, {}, path=foreign_path)
    assert missing.status_code == 404, missing.text
    assert snapshot(c) == before
    query = await patch(c, {}, path=item_path(c) + "?tenant_id=foreign")
    assert query.status_code == 422, query.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [], [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)],
    [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_key_is_required_unique_and_bounded(item_http, headers):
    c = item_http
    before = snapshot(c)
    response = await patch(c, {}, headers=headers)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.item_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [
    "authentication", "suite", "core", "rate", "membership", "scope", "revoked",
])
async def test_existing_security_gates_are_preserved(item_http, gate):
    c = item_http
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
async def test_replay_rechecks_current_authority(item_http):
    c = item_http
    assert (await patch(c, {"required": False})).status_code == 200
    c.grant.status = "revoked"
    before = snapshot(c)
    response = await patch(c, {"required": False})
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.item_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_http_failure_rolls_back_item_draft_audit_and_receipt(item_http, failure):
    c = item_http
    if failure == "audit":
        c.fail_audit = True
    elif failure == "complete":
        c.fail_complete = "error"
    else:
        c.session.fail_commit = True
    before, commits = snapshot(c), c.session.commits
    response = await patch(c, {"required": False})
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_missing_replay_config_is_sanitized_and_read_still_works(item_http, monkeypatch):
    c = item_http
    c.app.dependency_overrides.pop(routes.get_draft_item_workflow)

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
async def test_wire_openapi_has_both_routes_and_exact_version_bounds(item_http):
    c = item_http
    response = await call(c, "GET", "/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    prefix = (
        "/api/v1/construction/settings/measurement-templates/"
        "{template_id}/drafts/{version_id}/")
    schemas = document["components"]["schemas"]
    for segment, title, allowed in (
        ("sections", "TemplateDraftSectionPatchRequest", routes.SECTION_FIELDS),
        ("questions", "TemplateDraftQuestionPatchRequest", routes.QUESTION_FIELDS),
    ):
        operation = document["paths"][prefix + segment + "/{item_id}"]["patch"]
        keys = [p for p in operation["parameters"] if p["name"] == "Idempotency-Key"]
        assert len(keys) == 1 and keys[0]["required"] is True
        body = next(s for s in schemas.values() if s.get("title") == title)
        assert body["additionalProperties"] is False
        assert set(body["required"]) == {"expected_row_version"}
        field = body["properties"]["expected_row_version"]
        assert type(field["maximum"]) is int
        assert field["maximum"] == 2**63 - 1
        assert field["minimum"] == 1 and field["format"] == "int64"
        names = body["properties"]["fields"]["propertyNames"]["enum"]
        assert set(names) == set(allowed)
