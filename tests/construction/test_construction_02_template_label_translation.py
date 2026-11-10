"""Exact canonical labels through the existing local translation workflow."""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update

from api.auth import get_current_actor
from api.errors import ApiHttpError
from api.routes import construction_organization_templates as routes
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.module_access import ModuleDisabledError, SuiteDisabledError
from services.construction.construction_content_translation import (
    ContentTranslationRecord as Record,
    ContentTranslationStatus as Status,
)
from services.construction.construction_template_translation_source import (
    template_label_field, is_template_text_field,
)
from services.construction.construction_organization_template_definitions import (
    validate_organization_template_definition,
)
from test_construction_02_template_translation_http import (
    case, onboarding, catalog_case, copy_case, read_case, source_case,
    workflow_case, translation_http, get, cache_rows, domain_snapshot, PREFIX,
)


def label_path(c, key):
    return c.version_path + "/labels/" + key


async def install_help_fixture(c, *, key="Area.Help-1", supplied=None, text="Measure the clear area"):
    # Explicit local fixture setup only. No production publication or native
    # immutability/concurrency acceptance is claimed by this test.
    if not c.sync.in_transaction():
        c.sync.begin()
    state = await c.reader.get_version(
        actor=c.actor, version_id=c.base.template_version_id)
    body = state.definition.payload()
    body["sections"][0]["questions"][1]["help_text_key"] = key
    body["labels"]["en"][key] = text
    if supplied is not None:
        body["labels"]["uk"] = {key: supplied}
    checked = validate_organization_template_definition(body, source_language="en")
    c.sync.execute(update(c.copy_repo.questions).where(
        c.copy_repo.questions.c.template_version_id == c.base.template_version_id,
        c.copy_repo.questions.c.code == "AREA",
    ).values(help_text_key=key))
    c.sync.execute(update(c.copy_repo.versions).where(
        c.copy_repo.versions.c.id == c.base.template_version_id
    ).values(labels=body["labels"], content_hash=checked.content_hash))
    c.sync.commit()
    return key


@pytest.mark.asyncio
@pytest.mark.parametrize("key,original", [
    ("demo", "Demolition"),
    ("area", "Measured area"),
    ("yes", "Yes"),
    ("gate", "Any work in this phase?"),
])
async def test_section_question_and_option_text_use_exact_label_cache(
    translation_http, key, original,
):
    c = translation_http
    before, commits = domain_snapshot(c), c.session.commits
    response = await get(c, path=label_path(c, key))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["label_key"] == key
    assert data["field_name"] == template_label_field(key)
    assert data["original_text"] == original
    assert data["display_text"] == "Переклад"
    assert data["source_language"] == "en" and data["target_language"] == "uk"
    assert data["origin"] == "GENERATED" and data["cache_written"] is True
    assert data["translation_status"] == "AUTO_TRANSLATED"
    c.provider.translate.assert_awaited_once_with(
        text=original, source_language="en", target_language="uk")
    assert c.session.commits == commits + 1
    assert domain_snapshot(c) == before

    saved = cache_rows(c)
    second = await get(c, path=label_path(c, key))
    assert second.status_code == 200, second.text
    assert second.json()["data"]["origin"] == "CACHE"
    assert second.json()["data"]["cache_written"] is False
    assert second.json()["data"]["display_text"] == data["display_text"]
    assert cache_rows(c) == saved and domain_snapshot(c) == before
    c.provider.translate.assert_awaited_once()
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_help_key_with_case_dot_and_hyphen_is_preserved(translation_http):
    c = translation_http
    key = await install_help_fixture(c)
    before = domain_snapshot(c)
    response = await get(c, path=label_path(c, key))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["label_key"] == "Area.Help-1"
    assert data["field_name"] == template_label_field(key)
    assert data["original_text"] == "Measure the clear area"
    assert len(data["field_name"]) == 70
    assert domain_snapshot(c) == before
    c.provider.translate.assert_awaited_once_with(
        text="Measure the clear area", source_language="en", target_language="uk")


@pytest.mark.asyncio
async def test_supplied_help_text_does_not_invent_verified_cache_metadata(translation_http):
    c = translation_http
    key = await install_help_fixture(c, supplied="Готова підказка")
    response = await get(c, path=label_path(c, key))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["display_text"] == "Готова підказка"
    assert data["origin"] == "PUBLISHED_LABEL"
    assert data["translation_status"] is None
    assert data["cache_written"] is False and cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_label_cache_is_reused_without_provider(translation_http):
    c = translation_http
    if not c.sync.in_transaction():
        c.sync.begin()
    context = await c.source_service.read_for_actor(
        actor=c.actor, version_id=c.base.template_version_id,
        field_name=template_label_field("area"))
    record = Record(
        key=context.source.key_for("uk"), translated_text="Перевірена площа",
        translation_status=Status.AUTO_TRANSLATED, translated_at=c.now)
    saved = await c.translation_repo.insert_automatic(
        tenant_id=c.tenant, record=record, as_of=c.now)
    c.sync.execute(update(c.translation_table).where(
        c.translation_table.c.id == saved["id"]
    ).values(translation_status="VERIFIED", reviewed_by=c.user))
    c.sync.commit()
    before = cache_rows(c)
    response = await get(c, path=label_path(c, "area"))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["display_text"] == "Перевірена площа"
    assert response.json()["data"]["translation_status"] == "VERIFIED"
    assert cache_rows(c) == before
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_equal_text_under_distinct_keys_is_not_aliased(translation_http):
    c = translation_http
    key = await install_help_fixture(c, text="Measured area")
    first = await get(c, path=label_path(c, "area"))
    second = await get(c, path=label_path(c, key))
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["original_text"] == second.json()["data"]["original_text"]
    assert first.json()["data"]["field_name"] != second.json()["data"]["field_name"]
    assert len(cache_rows(c)) == 2
    assert c.provider.translate.await_count == 2


@pytest.mark.asyncio
async def test_machine_definition_remains_unchanged(translation_http):
    c = translation_http
    before = await get(c, path=c.version_path)
    assert before.status_code == 200, before.text
    assert (await get(c, path=label_path(c, "area"))).status_code == 200
    after = await get(c, path=c.version_path)
    assert after.status_code == 200, after.text
    assert after.json()["data"] == before.json()["data"]
    question = after.json()["data"]["definition"]["sections"][0]["questions"][1]
    assert question["formula_type"] == "DIRECT" and question["unit_code"] == "M2"
    assert question["show_if"] == {
        "question_code": "DEMO_GATE", "operator": "EQ", "value": "YES"}
    options = after.json()["data"]["definition"]["sections"][0]["questions"][0]["options"]
    assert [option["value"] for option in options] == ["NO", "YES", "NEEDS_CLARIFICATION"]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["Area", "ar", "missing", "DIRECT", "M2", "DEMO_GATE", "YES"])
async def test_nonlabels_prefixes_and_case_changes_are_not_translated(translation_http, key):
    c = translation_http
    response = await get(c, path=label_path(c, key))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["1area", "bad key", "a" * 129, "__proto__", "area+extra"])
async def test_invalid_label_keys_are_rejected_before_workflow(translation_http, key):
    c = translation_http
    response = await get(c, path=label_path(c, key))
    assert response.status_code == 422, response.text
    c.workflow_calls.assert_not_awaited()
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_caller_language_and_source_body_are_not_accepted(translation_http):
    c = translation_http
    response = await get(c, path=label_path(c, "area") + "?language=en")
    assert response.status_code == 422, response.text
    response = await get(
        c, path=label_path(c, "area"), content='{"text":"caller text"}',
        headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    c.workflow_calls.assert_not_awaited()
    assert cache_rows(c) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["authentication", "suite", "core", "rate", "membership", "scope"])
async def test_label_endpoint_keeps_shared_authorization(translation_http, gate):
    c = translation_http
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
        c.module.require_enabled.side_effect = ModuleDisabledError()
    elif gate == "rate":
        c.rates.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected = 429
    elif gate == "membership":
        c.membership = False
    else:
        c.grant.role, c.grant.scope_type = "OBSERVER", "project"
    response = await get(c, path=label_path(c, "area"))
    assert response.status_code == expected, response.text
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["language", "grant"])
async def test_late_context_change_does_not_persist_label_translation(translation_http, change):
    c = translation_http
    async def changed(**values):
        if change == "language":
            c.language = "it"
        else:
            c.grant.status = "revoked"
        return "Переклад"
    c.provider.translate.side_effect = changed
    response = await get(c, path=label_path(c, "area"))
    assert response.status_code == (409 if change == "language" else 403), response.text
    assert cache_rows(c) == ()


@pytest.mark.asyncio
async def test_cache_commit_failure_rolls_back_label_insert(translation_http):
    c = translation_http
    c.session.fail_commit = True
    before, commits = domain_snapshot(c), c.session.commits
    response = await get(c, path=label_path(c, "area"))
    assert response.status_code == 500, response.text
    assert cache_rows(c) == () and domain_snapshot(c) == before
    assert c.session.commits == commits


@pytest.mark.asyncio
async def test_profile_source_language_returns_original_label(translation_http):
    c = translation_http
    c.language = "en"
    response = await get(c, path=label_path(c, "yes"))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["original_text"] == data["display_text"] == "Yes"
    assert data["origin"] == "ORIGINAL" and data["cache_written"] is False
    assert cache_rows(c) == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_openapi_keeps_header_contract_and_describes_exact_labels(translation_http):
    c = translation_http
    response = await get(c, path="/api/v1/openapi.json")
    assert response.status_code == 200, response.text
    document = response.json()
    path = PREFIX + "/versions/{version_id}/labels/{label_key}"
    operation = document["paths"][path]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {"version_id", "label_key"}
    assert parameters["label_key"]["schema"]["maxLength"] == 128
    assert "requestBody" not in operation
    schemas = document["components"]["schemas"]
    label = next(item for item in schemas.values()
                 if item.get("title") == "TemplateTranslatedLabelData")
    header = next(item for item in schemas.values()
                  if item.get("title") == "TemplateTranslatedTextData")
    assert set(header["properties"]["field_name"]["enum"]) == {"title", "description"}
    assert label["properties"]["field_name"]["maxLength"] == 70
    for name in ("source_version", "cache_row_version"):
        integer = next(
            item for item in label["properties"][name]["anyOf"]
            if item.get("type") == "integer")
        assert type(integer["maximum"]) is int
        assert integer["maximum"] == 2**63 - 1
        assert integer["format"] == "int64"


def test_label_field_identity_is_exact_and_storage_compatible():
    first = template_label_field("Area.Help-1")
    assert len(first) == 70 and is_template_text_field(first)
    assert first == template_label_field("Area.Help-1")
    assert first != template_label_field("area.help-1")
    assert not is_template_text_field("labels.area")
