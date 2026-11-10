"""Canonical template completion with local transactions; no native DB access."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.dialects import postgresql

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.routes import construction_onboarding as routes
from api.routes.construction import (
    get_construction_access_context, get_construction_scoped_session,
)
from database.repositories.construction.construction_onboarding_templates import (
    ConstructionOnboardingTemplateRepository as Repository,
)
from services.api_rate_limits import (
    ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore,
)
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_onboarding import (
    ConstructionOnboardingSettingsVerifier,
    ConstructionOnboardingStep as Step,
    ConstructionOnboardingStepNotReady as NotReady,
    ConstructionOnboardingOperationError as Operation,
    ConstructionOnboardingCompletionUnavailable as Unavailable,
    build_construction_onboarding_service,
)
from services.construction.construction_onboarding_templates import (
    ConstructionOnboardingTemplateVerifier as Verifier,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_02_organization_template_read import (
    case, onboarding, catalog_case, copy_case, read_case, add_version,
)
from test_construction_02_template_copy import copy as create_copy, snapshot
from test_construction_02_onboarding_progress import begin, start, change


@pytest.fixture
def template_progress(read_case):
    c = read_case
    c.template_candidates = Repository(c.session)
    c.template_candidates.templates = c.copy_repo.templates
    c.template_candidates.versions = c.copy_repo.versions
    c.template_verifier = Verifier(
        session=c.session, repository=c.template_candidates,
        template_service=c.reader)
    c.other_verifier = NS(
        session=c.session,
        verify_step=AsyncMock(side_effect=Unavailable("Other step verifier not configured.")))
    c.progress.verifier = ConstructionOnboardingSettingsVerifier(
        session=c.session,
        company_service=NS(session=c.session, get_profile=AsyncMock()),
        pricing_verifier=c.other_verifier,
        template_verifier=c.template_verifier)
    yield c


async def open_templates(c):
    state = (await start(c)).progress
    return (await change(
        c, "move_to_step", state, Step.MEASUREMENT_TEMPLATES)).progress


async def verify(c):
    begin(c)
    return await c.template_verifier.verify_step(
        actor=c.actor, step=Step.MEASUREMENT_TEMPLATES)


@pytest.mark.asyncio
async def test_starter_catalog_without_organization_copy_cannot_complete(template_progress):
    c = template_progress
    state = await open_templates(c)
    before, commits = snapshot(c), c.session.commits
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert snapshot(c) == before and c.session.commits == commits
    c.other_verifier.verify_step.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_one_published_copy_completes_only_its_step_atomically(template_progress, role):
    c = template_progress
    c.grant.role = role
    copied = await create_copy(c)
    begin(c)
    progress = await c.progress.read_progress(actor=c.actor)
    assert not progress.started
    state = await open_templates(c)
    before, commits = snapshot(c), c.session.commits
    events = c.events.create_event.await_count
    result = await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert result.changed and result.progress.row_version == state.row_version + 1
    assert result.progress.current_step is Step.MEASUREMENT_TEMPLATES
    states = result.progress.states_dict()
    assert states["MEASUREMENT_TEMPLATES"] == "COMPLETED"
    assert all(value == "PENDING" for key, value in states.items()
               if key != "MEASUREMENT_TEMPLATES")
    assert result.progress.is_complete is False
    assert c.session.commits == commits + 1
    assert c.events.create_event.await_count == events + 1
    after = snapshot(c)
    assert after[:-2] == before[:-2]
    assert len(after[-1]) == len(before[-1]) + 1
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_onboarding_progress_updated"
    assert event["entity_id"] == c.tenant
    assert event["trace_id"] == "onboarding-test"
    assert event["payload"]["operation"] == "onboarding_complete"
    assert copied.section_count == 2 and copied.question_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["disabled", "draft", "future"])
async def test_unavailable_organization_template_does_not_complete(template_progress, condition):
    c = template_progress
    copied = await create_copy(c)
    if condition == "disabled":
        c.sync.execute(update(c.copy_repo.templates).where(
            c.copy_repo.templates.c.id == copied.template_id).values(active=False))
    else:
        values = (
            {"status": "DRAFT", "published_at": None, "content_hash": None}
            if condition == "draft"
            else {"published_at": c.now + timedelta(days=1)}
        )
        c.sync.execute(update(c.copy_repo.versions).where(
            c.copy_repo.versions.c.id == copied.template_version_id).values(**values))
    c.sync.commit()
    state = await open_templates(c)
    before = snapshot(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_inactive_copy_does_not_hide_another_active_copy(template_progress):
    c = template_progress
    first = await create_copy(c, code="INACTIVE_COPY")
    await create_copy(c, code="ACTIVE_COPY")
    c.sync.execute(update(c.copy_repo.templates).where(
        c.copy_repo.templates.c.id == first.template_id).values(active=False))
    c.sync.commit()
    assert await verify(c) is True


@pytest.mark.asyncio
async def test_source_disable_does_not_invalidate_organization_copy(template_progress):
    c = template_progress
    await create_copy(c)
    c.sync.execute(update(c.catalog_templates).values(active=False))
    c.sync.commit()
    before, commits = snapshot(c), c.session.commits
    assert await verify(c) is True
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_organization_owned_new_version_needs_no_live_source(template_progress):
    c = template_progress
    copied = await create_copy(c)
    version = await add_version(c, copied, number=2, org=True)
    c.reader.get_version = AsyncMock(wraps=c.reader.get_version)
    assert await verify(c) is True
    assert c.reader.get_version.await_args.kwargs["version_id"] == version


@pytest.mark.asyncio
async def test_foreign_tenant_candidate_query_does_not_see_current_copy(template_progress):
    c = template_progress
    await create_copy(c)
    begin(c)
    assert await c.template_candidates.get_active_published_candidate(
        tenant_id=c.foreign, as_of=c.now) is None


@pytest.mark.asyncio
async def test_current_noop_rechecks_template_and_stale_version_fails_first(template_progress):
    c = template_progress
    copied = await create_copy(c)
    state = await open_templates(c)
    state = (await change(
        c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)).progress
    c.template_candidates.get_active_published_candidate = AsyncMock(
        wraps=c.template_candidates.get_active_published_candidate)
    before = snapshot(c)
    result = await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert not result.changed and result.progress == state
    assert snapshot(c) == before
    c.template_candidates.get_active_published_candidate.assert_awaited_once()

    c.template_candidates.get_active_published_candidate.reset_mock()
    with pytest.raises(ConstructionRowVersionConflictError):
        await change(
            c, "complete_step", replace(state, row_version=state.row_version - 1),
            Step.MEASUREMENT_TEMPLATES)
    c.template_candidates.get_active_published_candidate.assert_not_awaited()

    c.sync.execute(update(c.copy_repo.templates).where(
        c.copy_repo.templates.c.id == copied.template_id).values(active=False))
    c.sync.commit()
    before = snapshot(c)
    with pytest.raises(NotReady):
        await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "commit", "cancel"])
async def test_failure_rolls_back_progress_and_audit_together(template_progress, failure):
    c = template_progress
    await create_copy(c)
    state = await open_templates(c)
    before, commits = snapshot(c), c.session.commits
    if failure == "audit":
        c.fail_audit = True
    elif failure == "commit":
        c.session.fail_commit = True
    else:
        original = c.events.create_event.side_effect
        async def cancelled(**values):
            await original(**values)
            raise asyncio.CancelledError()
        c.events.create_event.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else Operation):
        await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert snapshot(c) == before and c.session.commits == commits


@pytest.mark.asyncio
async def test_caller_can_roll_back_completion_without_committing_templates(template_progress):
    c = template_progress
    await create_copy(c)
    state = await open_templates(c)
    before, commits = snapshot(c), c.session.commits
    result = await change(
        c, "complete_step", state, Step.MEASUREMENT_TEMPLATES,
        manage_transaction=False)
    assert result.changed and c.session.commits == commits
    await c.session.rollback()
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "grant"])
async def test_fresh_authority_after_canonical_read_is_required(template_progress, failure):
    c = template_progress
    await create_copy(c)
    state = await open_templates(c)
    original = c.reader.get_version
    async def revoked(**values):
        result = await original(**values)
        if failure == "membership":
            c.membership = False
        else:
            c.grant.status = "revoked"
        return result
    c.reader.get_version = revoked
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_corrupt_published_content_cannot_complete(template_progress):
    c = template_progress
    copied = await create_copy(c)
    c.sync.execute(update(c.copy_repo.versions).where(
        c.copy_repo.versions.c.id == copied.template_version_id
    ).values(content_hash="0" * 64))
    c.sync.commit()
    state = await open_templates(c)
    before = snapshot(c)
    with pytest.raises(Operation):
        await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign", "missing_field", "wrong_root"])
async def test_malformed_candidate_never_completes(template_progress, failure):
    c = template_progress
    copied = await create_copy(c)
    candidate = {
        "tenant_id": c.tenant, "template_id": copied.template_id,
        "version_id": copied.template_version_id,
    }
    if failure == "foreign":
        candidate["tenant_id"] = c.foreign
    elif failure == "missing_field":
        candidate.pop("version_id")
    else:
        candidate["template_id"] = uuid4()
    c.template_candidates.get_active_published_candidate = AsyncMock(return_value=candidate)
    state = await open_templates(c)
    before = snapshot(c)
    with pytest.raises(Operation):
        await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_reader_commit_is_rejected_before_progress_write(template_progress):
    c = template_progress
    await create_copy(c)
    state = await open_templates(c)
    original = c.reader.get_version
    async def committed(**values):
        result = await original(**values)
        await c.session.commit()
        return result
    c.reader.get_version = committed
    before = snapshot(c)
    with pytest.raises(Operation):
        await change(c, "complete_step", state, Step.MEASUREMENT_TEMPLATES)
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_repository_sql_has_tenant_filters_bound_and_share_locks():
    statements = []
    class Capture:
        async def execute(self, statement):
            statements.append(statement)
            return NS(mappings=lambda: NS(one_or_none=lambda: None))
    repository = Repository(Capture())
    from datetime import datetime, timezone
    tenant = uuid4()
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    assert await repository.get_active_published_candidate(
        tenant_id=tenant, as_of=now) is None
    compiled = statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "construction_measurement_templates.tenant_id =" in sql
    assert "construction_measurement_template_versions.tenant_id =" in sql
    assert "active IS true" in sql and "published_at <=" in sql
    assert "FOR SHARE OF construction_measurement_templates, construction_measurement_template_versions" in sql
    assert "LIMIT" in sql
    assert tenant in compiled.params.values()
    assert "PUBLISHED" in compiled.params.values()
    assert 1 in compiled.params.values()


def test_default_factory_wires_shared_verifier_without_opening_connections(template_progress):
    c = template_progress
    built = build_construction_onboarding_service(
        session=c.session, now_provider=lambda: c.now)
    verifier = built.verifier.template_verifier
    assert isinstance(verifier, Verifier)
    assert verifier.session is c.session
    assert verifier.repository.session is c.session
    assert verifier.templates.session is c.session
    assert verifier.templates.authorizer.verifier is None
    assert built.verifier.tax_verifier is not None
    assert built.verifier.localization_verifier is not None


@pytest.mark.asyncio
async def test_template_verifier_does_not_claim_other_steps(template_progress):
    with pytest.raises(Unavailable):
        await template_progress.template_verifier.verify_step(
            actor=template_progress.actor, step=Step.WORK_PRICES)


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, True])
async def test_existing_complete_http_uses_canonical_verifier(template_progress, ready):
    c = template_progress
    if ready:
        await create_copy(c)
    state = await open_templates(c)
    app = create_app()
    async def session():
        begin(c)
        yield c.session
    async def context():
        return await c.progress.resolver.resolve(actor=c.actor)
    app.dependency_overrides[get_current_actor] = lambda: c.actor
    app.dependency_overrides[get_construction_scoped_session] = session
    app.dependency_overrides[get_construction_access_context] = context
    app.dependency_overrides[routes.get_onboarding_service] = lambda: c.progress
    app.dependency_overrides[get_api_request_rate_limit_service] = lambda: (
        ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore()))
    app.dependency_overrides[get_tenant_suite_access_service] = lambda: NS(
        require_enabled=AsyncMock())
    app.dependency_overrides[get_tenant_module_access_service] = lambda: NS(
        require_enabled=AsyncMock())
    before = snapshot(c)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/construction/settings/onboarding/steps/MEASUREMENT_TEMPLATES/complete",
            json={"expected_row_version": state.row_version},
            headers={"X-Request-ID": "template-step-http"})
    assert response.status_code == (200 if ready else 409), response.text
    if ready:
        data = response.json()["data"]
        assert data["step_states"]["MEASUREMENT_TEMPLATES"] == "COMPLETED"
        assert data["row_version"] == state.row_version + 1
        assert data["is_complete"] is False
    else:
        assert response.json()["error"]["code"] == "ONBOARDING_STEP_NOT_READY"
        assert snapshot(c) == before
