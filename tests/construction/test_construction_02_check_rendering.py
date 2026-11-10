"""Check rendering over local SQLite cache and a fake translation provider only."""
import asyncio
from dataclasses import replace
from decimal import Decimal, localcontext
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from database.models import Language, TranslationCache
from database.repositories.platform_catalog_translations import PlatformCatalogTranslationRepository
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.platform_catalog_localization import PlatformCatalogLocalizer, CatalogLabel
from services.construction import construction_check_rendering as rendering
from services.construction.construction_checks import (
    ConstructionCheckContext as Context, ConstructionCheckCode as Code,
    ConstructionCheckFinding as Finding, collect_construction_checks)
from test_construction_02_tax_profiles import case


@pytest.fixture
def rendered(case):
    c = case
    c.sync.rollback()
    c.languages = Language.__table__.to_metadata(c.table.metadata)
    c.cache = TranslationCache.__table__.to_metadata(c.table.metadata)
    c.languages.create(c.sync.get_bind())
    c.cache.create(c.sync.get_bind())
    for code in ("en", "uk", "ru", "pt", "es", "it", "pl", "de", "nl", "fi", "fr"):
        c.sync.execute(insert(c.languages), dict(code=code, name=code, native_name=None, is_active=code != "fr"))
    c.sync.commit()
    c.language, c.calls, c.deny_at, c.change_at = "uk", 0, None, None
    c.actor = ApiActorContext(c.user, c.tenant, None, ("client",), "untrusted", None, "active")
    c.context = Context(c.tenant, c.user, "construction.estimate.review", uuid4())
    c.report = collect_construction_checks(
        context=c.context, findings=[Finding(c.context, code) for code in Code])
    users = c.table.metadata.tables["users"]

    async def fresh(*, user_id, tenant_id):
        c.calls += 1
        exists = c.sync.scalar(select(users.c.id).where(users.c.id == user_id))
        if exists is None or tenant_id != c.tenant or not c.membership or (c.deny_at and c.calls >= c.deny_at):
            raise ApiIdentityAccessError("Private identity state.")
        code = "it" if c.change_at and c.calls >= c.change_at else c.language
        return replace(c.actor, language_code=code)

    c.identity = NS(session=c.session, require_actor=AsyncMock(side_effect=fresh))

    async def translate(**values):
        assert values["source_language"] == "en" and values["target_language"] == "fi"
        return "fi " + values["text"]

    c.provider = NS(provider_name="local-check-test", timeout_seconds=5, translate=AsyncMock(side_effect=translate))
    c.localizer = PlatformCatalogLocalizer(
        repository=PlatformCatalogTranslationRepository(c.session), provider=c.provider)
    c.renderer = rendering.ConstructionCheckRenderingService(
        session=c.session, identity=c.identity, localizer=c.localizer)
    yield c


def cache_rows(c):
    rows = tuple(dict(row) for row in c.sync.execute(select(c.cache).order_by(c.cache.c.id)).mappings())
    c.sync.rollback()
    return rows


def business_rows(c):
    tables = [table for table in c.table.metadata.sorted_tables if table.name != "translation_cache"]
    result = tuple((table.name, tuple(tuple(row) for row in c.sync.execute(select(table)))) for table in tables)
    c.sync.rollback()
    return result


async def render(c, *, report=None, actor=None):
    return await c.renderer.render_for_actor(
        actor=c.actor if actor is None else actor, report=c.report if report is None else report)


@pytest.mark.asyncio
@pytest.mark.parametrize("language,level", [
    ("uk", "Помилка"), ("ru", "Ошибка"), ("en", "Error"), ("pt", "Erro"),
    ("es", "Error"), ("it", "Errore"), ("pl", "Błąd"), ("de", "Fehler"), ("nl", "Fout"),
])
async def test_supplied_text_uses_fresh_profile_without_cache_writes_or_fallback(rendered, language, level):
    c = rendered
    c.language = language
    before = business_rows(c)
    result = await render(c)
    data = result.to_payload()
    assert result.report is c.report and data["language_code"] == language and data["label_source_version"] == 1
    assert data["findings"][0]["severity_label"] == level
    assert all(row["message_status"] == row["severity_label_status"] == "provided" for row in data["findings"])
    assert data["is_blocked"] == c.report.is_blocked and data["requires_attention"] == c.report.requires_attention
    assert all(row["message"] != row["code"] and row["message"] != row["message_key"] for row in data["findings"])
    c.provider.translate.assert_not_awaited()
    assert c.session.commits == 0 and cache_rows(c) == () and business_rows(c) == before
    assert not c.sync.in_transaction()


@pytest.mark.asyncio
async def test_unbundled_active_language_uses_existing_versioned_cache(rendered):
    c = rendered
    c.language = "fi"
    first = await render(c)
    old = cache_rows(c)
    assert len(old) == 12 and c.provider.translate.await_count == 12 and c.session.commits == 1
    assert all(row["source_language"] == "en" and row["target_language"] == "fi" for row in old)
    assert all(item["message_status"] == item["severity_label_status"] == "translated"
               for item in first.to_payload()["findings"])
    second = await render(c)
    assert c.provider.translate.await_count == 12 and c.session.commits == 1 and cache_rows(c) == old
    assert second.to_payload()["language_code"] == "fi"
    assert [row["message"] for row in first.to_payload()["findings"]] == [
        row["message"] for row in second.to_payload()["findings"]]
    assert all(row["message_status"] == row["severity_label_status"] == "cached"
               for row in second.to_payload()["findings"])


@pytest.mark.asyncio
async def test_new_source_version_keeps_old_cache_unchanged(rendered, monkeypatch):
    c = rendered
    c.language = "fi"
    await render(c)
    old = cache_rows(c)
    version, source, supplied = rendering.check_label_source()
    monkeypatch.setattr(rendering, "check_label_source", lambda: (version + 1, source, supplied))
    result = await render(c)
    new = {row["id"]: row for row in cache_rows(c)}
    assert len(new) == 24 and c.provider.translate.await_count == 24 and result.source_version == version + 1
    assert all(new[row["id"]] == row for row in old)


@pytest.mark.asyncio
async def test_numbers_identifiers_and_blocking_flags_are_not_translated(rendered):
    c = rendered
    c.language = "fi"
    value = Decimal("-0.123456789012345678901234567890123456789")
    finding = Finding(c.context, Code.NEGATIVE_MARGIN, field_path="totals.margin", parameters={"margin": value})
    report = collect_construction_checks(context=c.context, findings=[finding])
    with localcontext() as precision:
        precision.prec = 1
        result = await render(c, report=report)
        data = result.to_payload()
    row = data["findings"][0]
    assert {key: row[key] for key in finding.to_payload()} == finding.to_payload()
    assert row["parameters"] == {"margin": str(value)} and not data["is_blocked"] and data["requires_attention"]
    assert c.provider.translate.await_count == 2
    for call in c.provider.translate.await_args_list:
        text = call.kwargs["text"]
        assert str(value) not in text and str(c.user) not in text and "totals.margin" not in text


@pytest.mark.asyncio
async def test_empty_report_needs_no_translation_provider(rendered):
    c = rendered
    c.language = "fi"
    report = collect_construction_checks(context=c.context, findings=[])
    result = await render(c, report=report)
    assert result.to_payload()["findings"] == [] and result.language_code == "fi"
    c.provider.translate.assert_not_awaited()
    assert c.session.commits == 0 and cache_rows(c) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["fr", "unknown"])
async def test_inactive_or_unknown_profile_language_is_not_replaced_with_english(rendered, language):
    c = rendered
    c.language = language
    with pytest.raises(rendering.ConstructionCheckRenderingLanguageError):
        await render(c)
    c.provider.translate.assert_not_awaited()
    assert cache_rows(c) == () and c.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["user_id", "tenant_id"])
async def test_report_context_mismatch_is_denied_before_identity_or_provider(rendered, part):
    c = rendered
    other = replace(c.context, **{part: uuid4()})
    report = collect_construction_checks(context=other, findings=[])
    with pytest.raises(rendering.ConstructionCheckRenderingDenied):
        await render(c, report=report)
    c.identity.require_actor.assert_not_awaited()
    c.provider.translate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [2, 3])
@pytest.mark.parametrize("failure", ["membership", "language"])
async def test_late_identity_or_language_change_rolls_back_all_pending_cache(rendered, phase, failure):
    c = rendered
    c.language = "fi"
    if failure == "membership":
        c.deny_at = phase
        expected = rendering.ConstructionCheckRenderingDenied
    else:
        c.change_at = phase
        expected = rendering.ConstructionCheckRenderingLanguageChanged
    with pytest.raises(expected):
        await render(c)
    assert c.calls == phase and cache_rows(c) == () and c.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "persist", "commit", "cancel"])
async def test_failures_and_cancellation_never_leave_partial_cache(rendered, failure):
    c = rendered
    c.language = "fi"
    expected = rendering.ConstructionCheckRenderingError
    if failure == "provider":
        c.provider.translate.side_effect = RuntimeError("PRIVATE_PROVIDER_DETAILS")
        expected = rendering.ConstructionCheckTranslationUnavailable
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        persist = c.localizer.persist
        async def fail(prepared):
            await persist(prepared)
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("PRIVATE_CACHE_DETAILS")
        c.localizer.persist = fail
        if failure == "cancel":
            expected = asyncio.CancelledError
    with pytest.raises(expected) as caught:
        await render(c)
    assert "PRIVATE_" not in str(caught.value)
    assert cache_rows(c) == () and c.session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["language", "version", "missing", "duplicate", "technical_text"])
async def test_malformed_translation_projection_is_rejected_before_cache_write(rendered, failure):
    c = rendered
    c.language = "fi"
    prepare = c.localizer.prepare
    async def malformed(**values):
        prepared = await prepare(**values)
        if failure == "language": return replace(prepared, language_code="en")
        if failure == "version": return replace(prepared, source_version=True)
        if failure == "missing": return replace(prepared, labels=prepared.labels[:-1])
        if failure == "duplicate": return replace(prepared, labels=(prepared.labels[0],) * len(prepared.labels))
        first = prepared.labels[0]
        return replace(prepared, labels=(CatalogLabel(first.key, "PRICE_MISSING", first.status), *prepared.labels[1:]))
    c.localizer.prepare = malformed
    with pytest.raises(rendering.ConstructionCheckRenderingError):
        await render(c)
    assert cache_rows(c) == () and c.session.commits == 0


@pytest.mark.asyncio
async def test_renderer_cannot_commit_or_rollback_a_callers_active_transaction(rendered):
    c = rendered
    c.sync.begin()
    before = c.session.rollbacks
    with pytest.raises(rendering.ConstructionCheckRenderingError, match="dedicated clean"):
        await render(c)
    assert c.sync.in_transaction() and c.session.rollbacks == before and c.session.commits == 0
    c.sync.rollback()
    c.identity.require_actor.assert_not_awaited()


def test_source_covers_every_rule_and_severity_in_required_and_existing_languages():
    version, source, supplied = rendering.check_label_source()
    assert type(version) is int and version == 1 and len(source) == 12
    assert {"ru", "pt", "en", "es", "it", "uk", "pl", "de", "nl"} <= set(supplied)
    assert all(set(labels) == set(source) for labels in supplied.values())
    assert all(isinstance(text, str) and 1 <= len(text) <= 120
               for labels in supplied.values() for text in labels.values())


@pytest.mark.asyncio
async def test_language_deactivated_after_translation_is_rechecked_before_persist(rendered):
    c = rendered
    c.language = "fi"
    original = c.localizer.repository.is_active_language
    reads = []
    async def active(language):
        actual = await original(language)
        reads.append(language)
        return actual if len(reads) == 1 else False
    c.localizer.repository.is_active_language = active
    with pytest.raises(rendering.ConstructionCheckRenderingLanguageError):
        await render(c)
    assert len(reads) == 2 and cache_rows(c) == () and c.session.commits == 0
