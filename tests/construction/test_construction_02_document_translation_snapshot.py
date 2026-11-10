"""Pure snapshot preparation with fake provider; no database or network."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction.construction_content_translation import (
    ConstructionContentTranslator,
    ContentTranslationSource as Source,
    ContentTranslationRecord as Record,
    ContentTranslationStatus as Status,
    ContentFieldKind as Kind,
    ContentTranslationContractError as ContractError,
    ContentTranslationUnavailable,
    PreparedContentTranslation,
)
from services.construction.construction_document_language import (
    ConstructionDocumentLanguagePlan,
)
from services.construction.construction_document_translation_snapshot import (
    ConstructionDocumentTranslationPreparer,
)

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def document():
    plan = ConstructionDocumentLanguagePlan(
        tenant_id=uuid4(), user_id=uuid4(),
        interface_language="uk", organization_default_language="it",
        organization_row_version=7, document_languages=("en", "pt"))
    source = Source(
        entity_type="construction.work_type_version",
        entity_id=uuid4(), field_name="description",
        source_language="en", source_version=3,
        original_text="  Wall preparation\nSecond line  ",
        field_kind=Kind.TEXT)
    provider = NS(translate=AsyncMock(return_value="Preparação das paredes"))
    translator = ConstructionContentTranslator(
        provider=provider, now_provider=lambda: NOW)
    return NS(
        plan=plan, source=source, provider=provider,
        translator=translator,
        preparer=ConstructionDocumentTranslationPreparer(translator=translator),
        version_id=uuid4())


async def prepare(c, **changes):
    values = {
        "language_plan": c.plan,
        "document_version_id": c.version_id,
        "document_version": 1,
        "sources": (c.source,),
        "records": (),
    }
    values.update(changes)
    return await c.preparer.prepare_for_version(**values)


def cached(c, *, status=Status.VERIFIED, source=None, target="pt", text="Reviewed"):
    source = c.source if source is None else source
    return Record(
        key=source.key_for(target), translated_text=text,
        translation_status=status, translated_at=NOW,
        reviewed_by=c.plan.user_id if status is Status.VERIFIED else None)


@pytest.mark.asyncio
async def test_bilingual_snapshot_preserves_source_and_explicit_language_order(document):
    c = document
    result = await prepare(c)
    payload = result.snapshot.payload()
    assert payload["tenant_id"] == str(c.plan.tenant_id)
    assert payload["document_version_id"] == str(c.version_id)
    assert payload["document_version"] == 1
    assert payload["document_languages"] == ["en", "pt"]
    assert "interface_language" not in payload
    assert "organization_default_language" not in payload
    field, = payload["fields"]
    assert field["original_text"] == c.source.original_text
    assert field["source_version"] == 3
    assert field["source_text_hash"] == sha256(
        c.source.original_text.encode("utf-8")).hexdigest()
    original, translated = field["translations"]
    assert original["target_language"] == "en"
    assert original["display_text"] == c.source.original_text
    assert original["translation_status"] is None
    assert translated["target_language"] == "pt"
    assert translated["display_text"] == "Preparação das paredes"
    assert translated["translation_status"] == "AUTO_TRANSLATED"
    assert translated["reviewed_by"] is None
    assert datetime.fromisoformat(translated["translated_at"]) == NOW
    assert len(result.pending) == 1
    c.provider.translate.assert_awaited_once_with(
        text=c.source.original_text, source_language="en", target_language="pt")


@pytest.mark.asyncio
async def test_single_document_language_does_not_use_profile_language(document):
    c = document
    plan = replace(c.plan, document_languages=("pt",))
    result = await prepare(c, language_plan=plan)
    assert result.snapshot.payload()["document_languages"] == ["pt"]
    assert len(result.snapshot.payload()["fields"][0]["translations"]) == 1
    assert c.provider.translate.await_args.kwargs["target_language"] == "pt"


@pytest.mark.asyncio
async def test_verified_cache_is_preserved_without_provider_or_pending_write(document):
    c = document
    record = cached(c)
    result = await prepare(c, records=(record,))
    translated = result.snapshot.payload()["fields"][0]["translations"][1]
    assert translated["display_text"] == "Reviewed"
    assert translated["translation_status"] == "VERIFIED"
    assert translated["reviewed_by"] == str(c.plan.user_id)
    assert result.pending == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("difference", ["version", "text", "target", "stale"])
async def test_old_or_stale_translation_does_not_enter_current_snapshot(document, difference):
    c = document
    source, target, status = c.source, "pt", Status.VERIFIED
    if difference == "version":
        source = replace(source, source_version=2)
    elif difference == "text":
        source = replace(source, original_text="Previous text")
    elif difference == "target":
        target = "it"
    else:
        status = Status.STALE
    record = cached(c, status=status, source=source, target=target, text="Old translation")
    result = await prepare(c, records=(record,))
    translated = result.snapshot.payload()["fields"][0]["translations"][1]
    assert translated["display_text"] == "Preparação das paredes"
    assert translated["translation_status"] == "AUTO_TRANSLATED"
    assert record.translated_text == "Old translation"
    assert record.translation_status is status
    assert len(result.pending) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [kind for kind in Kind if kind is not Kind.TEXT])
async def test_protected_fields_are_preserved_in_every_language(document, kind):
    c = document
    source = replace(c.source, field_kind=kind)
    result = await prepare(c, sources=(source,))
    field, = result.snapshot.payload()["fields"]
    assert field["field_kind"] == kind.value
    assert all(
        item["display_text"] == source.original_text
        and item["translation_status"] is None
        and item["reviewed_by"] is None
        for item in field["translations"])
    assert result.pending == ()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_numeric_text_is_not_converted_through_float(document):
    c = document
    exact = "9007199254740993.00000000000000000001"
    source = replace(c.source, field_kind=Kind.NUMBER, original_text=exact)
    result = await prepare(c, sources=(source,))
    field, = result.snapshot.payload()["fields"]
    assert field["original_text"] == exact
    assert all(item["display_text"] == exact for item in field["translations"])
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_source_reuses_preparation_and_one_pending_record(document):
    c = document
    result = await prepare(c, sources=(c.source, c.source))
    first, second = result.snapshot.payload()["fields"]
    assert first == second
    assert len(result.pending) == 1
    c.provider.translate.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_payload_is_detached_and_canonical_text_is_immutable(document):
    c = document
    result = await prepare(c)
    original_json = result.snapshot.canonical_json
    original_hash = result.snapshot.content_hash
    projection = result.snapshot.payload()
    projection["fields"][0]["translations"][1]["display_text"] = "Changed"
    assert result.snapshot.canonical_json == original_json
    assert result.snapshot.content_hash == original_hash
    assert result.snapshot.payload()["fields"][0]["translations"][1]["display_text"] != "Changed"
    assert original_hash == sha256(original_json.encode("utf-8")).hexdigest()
    with pytest.raises(FrozenInstanceError):
        result.snapshot.canonical_json = "{}"


@pytest.mark.asyncio
async def test_interface_or_default_changes_do_not_change_same_document_content(document):
    c = document
    first = await prepare(c)
    changed_plan = replace(
        c.plan, interface_language="es",
        organization_default_language="ru", organization_row_version=99)
    second = await prepare(
        c, language_plan=changed_plan, records=first.pending)
    assert second.pending == ()
    assert second.snapshot.canonical_json == first.snapshot.canonical_json
    assert second.snapshot.content_hash == first.snapshot.content_hash
    assert c.provider.translate.await_count == 1


@pytest.mark.asyncio
async def test_new_source_and_document_versions_leave_previous_snapshot_unchanged(document):
    c = document
    first = await prepare(c)
    original_json = first.snapshot.canonical_json
    original_hash = first.snapshot.content_hash
    newer = replace(c.source, source_version=4, original_text="New source")
    second = await prepare(
        c, document_version_id=uuid4(), document_version=2, sources=(newer,))
    assert second.snapshot.payload()["fields"][0]["source_version"] == 4
    assert second.snapshot.content_hash != original_hash
    assert first.snapshot.canonical_json == original_json
    assert first.snapshot.content_hash == original_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"language_plan": None},
    {"document_version_id": "caller-id"},
    {"document_version": True},
    {"document_version": 0},
    {"sources": ()},
    {"sources": []},
    {"records": []},
])
async def test_invalid_contract_is_rejected_before_provider(document, changes):
    c = document
    with pytest.raises(ContractError):
        await prepare(c, **changes)
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unrelated_cache_record_is_not_accepted(document):
    c = document
    unrelated = replace(c.source, entity_id=uuid4())
    with pytest.raises(ContractError):
        await prepare(c, records=(cached(c, source=unrelated),))
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_cannot_invent_verified_record(document):
    c = document
    forged = cached(c, text="Unreviewed provider output")
    original = c.translator.prepare

    async def fake(**values):
        if values["target_language"] == "en":
            return await original(**values)
        return PreparedContentTranslation(
            c.source, "pt", forged.translated_text, forged, None)

    c.translator.prepare = fake
    with pytest.raises(ContractError, match="supplied records"):
        await prepare(c)


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_and_cancellation_propagates(document):
    c = document
    c.provider.translate.side_effect = RuntimeError("private provider credentials")
    with pytest.raises(ContentTranslationUnavailable) as captured:
        await prepare(c)
    assert "private provider credentials" not in str(captured.value)

    c.provider.translate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await prepare(c)


@pytest.mark.asyncio
async def test_bigint_versions_remain_exact_in_snapshot_json(document):
    c = document
    source = replace(
        c.source, source_version=2**63 - 1, field_kind=Kind.SYSTEM_CODE)
    result = await prepare(
        c, document_version=2**63 - 1, sources=(source,))
    payload = result.snapshot.payload()
    assert type(payload["document_version"]) is int
    assert payload["document_version"] == 2**63 - 1
    assert payload["fields"][0]["source_version"] == 2**63 - 1
    c.provider.translate.assert_not_awaited()
