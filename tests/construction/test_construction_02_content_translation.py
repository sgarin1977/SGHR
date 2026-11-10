"""Version-bound selection and preparation; no DB or provider network calls."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction.construction_content_translation import (
    ContentTranslationContractError as ContractError,
    ContentTranslationUnavailable as Unavailable,
    ContentTranslationStatus as Status,
    ContentFieldKind as Kind,
    ContentTranslationSource as Source,
    ContentTranslationRecord as Record,
    ConstructionContentTranslator as Translator,
    select_content_translation,
)

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


@pytest.fixture
def content():
    source = Source(
        entity_type="construction.project",
        entity_id=uuid4(),
        field_name="description",
        source_language="uk",
        source_version=3,
        original_text="  Підготовка стін\nДругий рядок  ",
        field_kind=Kind.TEXT)
    provider = NS(translate=AsyncMock(return_value="Wall preparation"))
    return NS(
        source=source, provider=provider,
        translator=Translator(provider=provider, now_provider=lambda: NOW))


def record(source, status, text="Cached", *, target="en", **changes):
    values = dict(
        key=source.key_for(target),
        translated_text=text,
        translation_status=status,
        translated_at=NOW,
        reviewed_by=uuid4() if status is Status.VERIFIED else None)
    values.update(changes)
    return Record(**values)


@pytest.mark.asyncio
async def test_verified_current_translation_precedes_automatic_without_provider(content):
    c = content
    verified = record(c.source, Status.VERIFIED, "Reviewed")
    automatic = record(c.source, Status.AUTO_TRANSLATED, "Automatic")
    result = await c.translator.prepare(
        source=c.source, target_language="en", records=(automatic, verified))
    assert result.display_text == "Reviewed"
    assert result.original_text == c.source.original_text
    assert result.record is verified and result.pending is None
    assert result.translation_status is Status.VERIFIED
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_automatic_cache_is_reused_without_provider(content):
    c = content
    cached = record(c.source, Status.AUTO_TRANSLATED)
    result = await c.translator.prepare(
        source=c.source, target_language="en", records=(cached,))
    assert result.record is cached and result.pending is None
    assert result.used_translation is True
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_translation_prepares_exact_version_record_without_mutating_source(content):
    c = content
    result = await c.translator.prepare(source=c.source, target_language="en")
    c.provider.translate.assert_awaited_once_with(
        text=c.source.original_text, source_language="uk", target_language="en")
    assert result.original_text == "  Підготовка стін\nДругий рядок  "
    assert result.display_text == "Wall preparation"
    assert result.pending is result.record
    assert result.record.key == c.source.key_for("en")
    assert result.translation_status is Status.AUTO_TRANSLATED
    assert result.record.translated_at == NOW
    assert result.record.reviewed_by is None
    with pytest.raises(FrozenInstanceError):
        c.source.original_text = "Changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("difference", ["version", "text", "target", "source_language", "stale"])
async def test_old_or_stale_record_never_satisfies_current_source(content, difference):
    c = content
    previous = c.source
    target, status = "en", Status.VERIFIED
    if difference == "version":
        previous = replace(previous, source_version=2)
    elif difference == "text":
        previous = replace(previous, original_text="Different original")
    elif difference == "target":
        target = "de"
    elif difference == "source_language":
        previous = replace(previous, source_language="ru")
    else:
        status = Status.STALE
    old = record(previous, status, "Old result", target=target)
    result = await c.translator.prepare(
        source=c.source, target_language="en", records=(old,))
    assert result.display_text == "Wall preparation"
    assert result.pending is not None
    assert old.translated_text == "Old result"
    assert old.translation_status is status
    c.provider.translate.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [kind for kind in Kind if kind is not Kind.TEXT])
async def test_protected_fields_are_preserved_without_provider_calls(content, kind):
    c = content
    source = replace(c.source, field_kind=kind)
    result = await c.translator.prepare(source=source, target_language="en")
    assert result.display_text == source.original_text
    assert result.original_text == source.original_text
    assert result.used_translation is False
    assert result.record is None and result.pending is None
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", " \n\t "])
async def test_empty_content_is_not_sent_to_provider(content, text):
    c = content
    source = replace(c.source, original_text=text)
    result = await c.translator.prepare(source=source, target_language="en")
    assert result.display_text == text and result.pending is None
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_language_preserves_exact_original(content):
    c = content
    result = await c.translator.prepare(source=c.source, target_language="uk")
    assert result.display_text == c.source.original_text
    assert result.translation_status is None
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("difference", ["entity_id", "entity_type", "field_name"])
async def test_records_from_another_field_fail_closed(content, difference):
    c = content
    other = replace(c.source, **{
        difference: uuid4() if difference == "entity_id"
        else "construction.area" if difference == "entity_type" else "name"})
    foreign = record(other, Status.VERIFIED, "Foreign field")
    with pytest.raises(ContractError):
        await c.translator.prepare(
            source=c.source, target_language="en", records=(foreign,))
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.VERIFIED, Status.AUTO_TRANSLATED])
async def test_ambiguous_current_records_are_not_chosen_arbitrarily(content, status):
    c = content
    first = record(c.source, status, "First")
    second = record(c.source, status, "Second")
    with pytest.raises(ContractError):
        await c.translator.prepare(
            source=c.source, target_language="en", records=(first, second))
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_is_sanitized_without_a_pending_record(content):
    c = content
    c.provider.translate.side_effect = RuntimeError("private provider configuration")
    with pytest.raises(Unavailable) as captured:
        await c.translator.prepare(source=c.source, target_language="en")
    assert "private provider configuration" not in str(captured.value)
    assert c.source.source_version == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "   ", 42, "bad\x00text"])
async def test_invalid_provider_output_cannot_become_a_cache_record(content, value):
    c = content
    c.provider.translate.return_value = value
    with pytest.raises(Unavailable):
        await c.translator.prepare(source=c.source, target_language="en")


@pytest.mark.asyncio
async def test_cancellation_propagates_without_persistence(content):
    c = content
    c.provider.translate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await c.translator.prepare(source=c.source, target_language="en")


@pytest.mark.asyncio
async def test_missing_provider_does_not_return_a_stale_translation(content):
    c = content
    translator = Translator()
    stale = record(c.source, Status.STALE)
    with pytest.raises(Unavailable):
        await translator.prepare(
            source=c.source, target_language="en", records=(stale,))


@pytest.mark.asyncio
@pytest.mark.parametrize("language", [None, "", "unknown-language", " en", "EN"])
async def test_invalid_target_never_silently_falls_back(content, language):
    c = content
    with pytest.raises(ContractError):
        await c.translator.prepare(source=c.source, target_language=language)
    c.provider.translate.assert_not_awaited()


def test_cache_identity_preserves_whitespace_and_changes_with_source_version(content):
    source = content.source
    key = source.key_for("en")
    assert key != replace(source, original_text=source.original_text.strip()).key_for("en")
    assert key != replace(source, source_version=4).key_for("en")
    assert key != source.key_for("de")


def test_record_requires_typed_status_and_aware_timestamp(content):
    with pytest.raises(ContractError):
        record(content.source, "VERIFIED")
    with pytest.raises(ContractError):
        record(content.source, Status.VERIFIED, translated_at=datetime(2026, 11, 9))


def test_source_version_and_field_classification_are_explicit(content):
    with pytest.raises(ContractError):
        replace(content.source, source_version=True)
    with pytest.raises(ContractError):
        replace(content.source, source_version=0)
    with pytest.raises(ContractError):
        replace(content.source, field_kind="TEXT")
