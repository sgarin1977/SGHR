"""Registered POST additions with local atomic workflows and encrypted replay."""
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
from services.construction.construction_template_draft_append import (
    ConstructionTemplateDraftAppendService,
)
from services.construction.construction_template_draft_append_workflow import (
    ConstructionTemplateDraftAppendWorkflow,
)
from database.repositories.construction.construction_template_draft_items import (
    ConstructionTemplateDraftItemRepository,
)
from test_construction_02_template_versions_http import (
    case, onboarding, catalog_case, copy_case, read_case, drafts, keyed,
    version_http, create, root, call, snapshot,
)
from test_construction_02_template_draft_append import (
    question, section, assert_old_rows_preserved,
)


@pytest.fixture
async def append_http(version_http):
    c = version_http
    repository = ConstructionTemplateDraftItemRepository(c.session)
    for name in ("templates", "versions", "sections", "questions"):
        setattr(repository, name, getattr(c.draft_repo, name))
    c.append_service = ConstructionTemplateDraftAppendService(
        session=c.session, repository=repository, content_service=c.content)
    c.append_workflow = ConstructionTemplateDraftAppendWorkflow(
        session=c.session, append_service=c.append_service,
        idempotency_service=c.idempotency)
    c.append_calls = AsyncMock(wraps=c.append_service.append_item)
    c.append_service.append_item = c.append_calls

    async def workflow(
        scoped=Depends(get_construction_scoped_session),
        key=Depends(routes.require_template_version_key),
    ):
        assert scoped is c.session
        return c.append_workflow

    c.app.dependency_overrides[routes.get_draft_append_workflow] = workflow
    c.created = await create(c)
    c.path = root(c) + "/" + c.created["version_id"]
    c.initial = await current(c)
    yield c


async def current(c):
    response = await call(c, "GET", c.path)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def endpoint(c, kind="QUESTION", parent=None):
    path = c.path + "/sections"
    if kind == "SECTION":
        return path
    parent = (
        c.initial["section_references"][0]["id"] if parent is None else str(parent))
    return path + "/" + parent + "/questions"


def payload(c, kind="QUESTION", *, expected=1):
    labels = deepcopy(c.initial["definition"]["labels"])
    language = c.initial["source_language"]
    labels[language]["extra"] = "Additional quantity"
    if kind == "SECTION":
        labels[language]["plaster"] = "Plaster preparation"
    return {
        "expected_row_version": expected,
        "section" if kind == "SECTION" else "question": (
            section() if kind == "SECTION" else question()),
        "labels": labels,
    }


async def post(
    c, *, kind="QUESTION", body=None, key="append-http-key",
    headers=None, path=None,
):
    return await call(
        c, "POST", endpoint(c, kind) if path is None else path,
        body=payload(c, kind) if body is None else body,
        key=key, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
async def test_creation_and_replay_keep_exact_ids_and_single_mutation(append_http, role, kind):
    c = append_http
    c.grant.role = role
    before, commits = snapshot(c), c.session.commits
    response = await post(c, kind=kind)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["kind"] == kind
    assert data["draft"]["changed"] is True
    assert data["draft"]["version_row_version"] == 2
    assert data["draft"]["created_by"] == c.initial["created_by"]
    assert len(data["section_ids"]) == (1 if kind == "SECTION" else 0)
    assert len(data["question_ids"]) == (2 if kind == "SECTION" else 1)
    assert data["parent_section_id"] == (
        None if kind == "SECTION"
        else c.initial["section_references"][0]["id"])
    assert c.session.commits == commits + 1

    kwargs = c.append_calls.await_args.kwargs
    assert kwargs["kind"] == kind
    assert kwargs["manage_transaction"] is False
    assert kwargs["trace_id"] == "template-version-http"
    saved = await current(c)
    question_ids = {
        q["id"] for s in saved["section_references"] for q in s["questions"]}
    assert set(data["question_ids"]).issubset(question_ids)
    assert_old_rows_preserved(before[0], snapshot(c)[0])
    assert saved["definition"]["labels"] == payload(c, kind)["labels"]

    after = snapshot(c)
    replay = await post(c, kind=kind)
    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == data
    assert snapshot(c) == after
    c.append_calls.assert_awaited_once()
    event = c.events.create_event.await_args.kwargs
    assert event["payload"]["operation"] == "add_draft_" + kind.lower()
    assert event["payload"]["new_question_ids"] == data["question_ids"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_omitted_labels_preserve_dictionary_and_contract_defaults_replay(append_http):
    c = append_http
    body = payload(c)
    body.pop("labels")
    body["question"]["label_key"] = "area"
    first = await post(c, body=body)
    assert first.status_code == 201, first.text
    assert "labels" not in c.append_calls.await_args.kwargs
    saved = await current(c)
    assert saved["definition"]["labels"] == c.initial["definition"]["labels"]
    canonical = routes.TemplateDraftQuestionCreateRequest.model_validate(
        body).model_dump(mode="json", exclude={"labels"})
    after = snapshot(c)
    replay = await post(c, body=canonical)
    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert snapshot(c) == after
    c.append_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["body", "labels", "parent", "version"])
async def test_key_conflict_prevents_second_addition(append_http, change):
    c = append_http
    assert (await post(c)).status_code == 201
    body, path = payload(c), None
    if change == "body":
        body["question"]["required"] = True
    elif change == "labels":
        body["labels"][c.initial["source_language"]]["extra"] = "Different"
    elif change == "parent":
        path = endpoint(c, parent=c.initial["section_references"][1]["id"])
    else:
        body["expected_row_version"] = 2
    before = snapshot(c)
    response = await post(c, body=body, path=path)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert snapshot(c) == before
    c.append_calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_version_and_duplicate_code_leave_no_new_content(append_http):
    c = append_http
    assert (await post(c)).status_code == 201
    before = snapshot(c)
    stale = await post(c, key="stale-addition-key")
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert snapshot(c) == before
    duplicate = await post(
        c, body=payload(c, expected=2), key="duplicate-code-key")
    assert duplicate.status_code == 422, duplicate.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_replay_after_publication_returns_original_receipt(append_http):
    c = append_http
    first = await post(c, kind="SECTION")
    assert first.status_code == 201, first.text
    published = await call(
        c, "POST", c.path + "/publish",
        body={"expected_row_version": 2}, key="append-later-publish-key")
    assert published.status_code == 200, published.text
    before = snapshot(c)
    replay = await post(c, kind="SECTION")
    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == first.json()["data"]
    assert snapshot(c) == before
    denied = await post(
        c, body=payload(c, expected=3), key="new-addition-after-publication")
    assert denied.status_code == 404, denied.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "phase_order", "missing_gate", "duplicate_section", "incomplete_labels",
])
async def test_full_structure_rules_apply_to_new_section(append_http, failure):
    c = append_http
    body = payload(c, "SECTION")
    if failure == "phase_order":
        body["section"]["work_phase_code"] = "FINAL_SEALING"
    elif failure == "missing_gate":
        body["section"]["questions"][0]["required"] = False
    elif failure == "duplicate_section":
        body["section"]["code"] = "DEMO"
    else:
        body["labels"] = {"en": {"extra": "Incomplete"}}
    before = snapshot(c)
    response = await post(c, kind="SECTION", body=body)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "tenant", "parent", "id", "missing_question", "null_question",
    "required_string", "sort_bool", "sort_overflow", "float_bound",
    "invalid_uuid", "labels_null", "version_bool", "version_string",
    "version_overflow",
])
async def test_closed_typed_body_cannot_bypass_shared_contract(append_http, change):
    c = append_http
    body = payload(c)
    if change == "tenant":
        body["tenant_id"] = str(uuid4())
    elif change == "parent":
        body["section_id"] = str(uuid4())
    elif change == "id":
        body["question"]["id"] = str(uuid4())
    elif change == "missing_question":
        body.pop("question")
    elif change == "null_question":
        body["question"] = None
    elif change == "required_string":
        body["question"]["required"] = "false"
    elif change == "sort_bool":
        body["question"]["sort_order"] = True
    elif change == "sort_overflow":
        body["question"]["sort_order"] = 2**31
    elif change == "float_bound":
        body["question"]["validation_schema"] = {"minimum": 0.1}
    elif change == "invalid_uuid":
        body["question"]["work_type_id"] = "invalid"
    elif change == "labels_null":
        body["labels"] = None
    else:
        body["expected_row_version"] = {
            "version_bool": True,
            "version_string": "1",
            "version_overflow": 2**63,
        }[change]
    before = snapshot(c)
    response = await post(c, body=body)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_foreign_parent_and_template_are_hidden_and_query_is_rejected(append_http):
    c = append_http
    before = snapshot(c)
    missing_parent = await post(c, path=endpoint(c, parent=uuid4()))
    assert missing_parent.status_code == 404, missing_parent.text
    foreign_path = endpoint(c).replace(str(c.base.template_id), str(uuid4()))
    missing_template = await post(c, path=foreign_path)
    assert missing_template.status_code == 404, missing_template.text
    query = await post(c, path=endpoint(c) + "?tenant_id=foreign")
    assert query.status_code == 422, query.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    [], [("Idempotency-Key", "")], [("Idempotency-Key", " ")],
    [("Idempotency-Key", "x" * 256)],
    [("Idempotency-Key", "a"), ("Idempotency-Key", "b")],
])
async def test_key_is_required_unique_and_bounded(append_http, headers):
    c = append_http
    before = snapshot(c)
    response = await post(c, headers=headers)
    assert response.status_code == 422, response.text
    assert snapshot(c) == before
    c.append_calls.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["SECTION", "QUESTION"])
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate", "membership", "scope"])
async def test_security_gates_apply_to_both_creation_routes(append_http, kind, gate):
    c = append_http
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
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    before = snapshot(c)
    response = await post(c, kind=kind)
    assert response.status_code == expected, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_replay_rechecks_current_grant(append_http):
    c = append_http
    assert (await post(c)).status_code == 201
    c.grant.status = "revoked"
    before = snapshot(c)
    response = await post(c)
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.append_calls.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_http_failure_rolls_back_structure_audit_and_receipt(append_http, failure):
    c = append_http
    if failure == "audit":
        c.fail_audit = True
    elif failure == "complete":
        c.fail_complete = "error"
    else:
        c.session.fail_commit = True
    before, commits = snapshot(c), c.session.commits
    response = await post(c, kind="SECTION")
    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "internal_error"
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_missing_configuration_is_sanitized_without_breaking_read(append_http, monkeypatch):
    c = append_http
    c.app.dependency_overrides.pop(routes.get_draft_append_workflow)

    def missing(cls, *args, **kwargs):
        raise ApiConfigurationError("private replay configuration")
    monkeypatch.setattr(routes.ApiIdempotencySettings, "from_env", classmethod(missing))
    before = snapshot(c)
    response = await post(c)
    assert response.status_code == 503, response.text
    assert "private replay configuration" not in response.text
    assert snapshot(c) == before
    assert (await call(c, "GET", c.path)).status_code == 200


@pytest.mark.asyncio
async def test_openapi_uses_shared_models_exact_counters_and_creation_status(append_http):
    c = append_http
    response = await call(c, "GET", "/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    schemas = document["components"]["schemas"]
    prefix = (
        "/api/v1/construction/settings/measurement-templates/"
        "{template_id}/drafts/{version_id}/sections")
    for suffix, title, name in (
        ("", "TemplateDraftSectionCreateRequest", "section"),
        ("/{section_id}/questions", "TemplateDraftQuestionCreateRequest", "question"),
    ):
        operation = document["paths"][prefix + suffix]["post"]
        assert "201" in operation["responses"]
        keys = [p for p in operation["parameters"] if p["name"] == "Idempotency-Key"]
        assert len(keys) == 1 and keys[0]["required"] is True
        body = next(s for s in schemas.values() if s.get("title") == title)
        assert body["additionalProperties"] is False
        assert set(body["required"]) == {"expected_row_version", name}
        field = body["properties"]["expected_row_version"]
        assert type(field["maximum"]) is int
        assert field["maximum"] == 2**63 - 1
        assert field["minimum"] == 1 and field["format"] == "int64"
        reference = body["properties"][name]["$ref"].split("/")[-1]
        shared = schemas[reference]
        assert shared["additionalProperties"] is False
        assert "code" in shared["properties"]
        if name == "section":
            assert "questions" in shared["properties"]
        else:
            assert set(shared["properties"]["question_type"]["enum"]) == {
                "BOOLEAN", "NUMBER", "TEXT", "SINGLE_CHOICE", "MULTI_CHOICE",
                "DATE", "PHOTO", "ELEMENT", "CALCULATED",
            }
