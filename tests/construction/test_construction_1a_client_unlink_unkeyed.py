from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import services.construction.construction_client_unlink_workflow as module
from services.api_identity import ApiIdentityAccessError
from services.construction.construction_clients import ConstructionClientOperationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import ConstructionPermissionDeniedError
from test_construction_1a_client_unlink_workflow import unlink_pipeline, unlink, edit, snapshot
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db


@pytest.fixture
async def unkeyed(unlink_pipeline):
    p, c = unlink_pipeline, unlink_pipeline.c
    p.workflow = module.ConstructionClientUnlinkWorkflow(session=c.session, unlink_service=p.service,
        idempotency_service=None, now_provider=lambda: c.now)
    return p


@pytest.mark.asyncio
async def test_owner_without_membership_or_selected_tenant_can_unlink_without_replay_configuration(unkeyed):
    p, c = unkeyed, unkeyed.c
    c.actor = replace(c.actor, tenant_id=None)
    c.access.has_active_platform_membership.side_effect = AssertionError('Self unlink must not require membership.')
    c.access.list_active_grants.side_effect = AssertionError('Self unlink must not require grants.')
    before = snapshot(p); audit_count = before[3]
    calls = p.ledger.calls
    result = await unlink(p, idempotency_key=None)
    after = snapshot(p)
    assert result.changed and result.linked_user_id is None and result.previous_linked_user_id == c.target
    assert result.row_version == 3 and result.audit_id is not None
    assert p.ledger.calls == calls and after[4] == before[4]
    assert after[1:3] == before[1:3] and after[3] == audit_count + 1
    assert c.audit_rows[-1].payload['authorization_method'] == 'authenticated_link_owner'
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_authorized_editor_without_key_has_version_conflict_and_current_version_noop(unkeyed):
    p = unkeyed
    first = await edit(p, idempotency_key=None)
    assert first.changed and first.row_version == 3
    before = snapshot(p)
    with pytest.raises(ConstructionRowVersionConflictError): await edit(p, idempotency_key=None)
    result = await edit(p, idempotency_key=None, expected_row_version=3)
    assert result.changed is False and result.row_version == 3 and result.audit_id is None
    assert snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['self', 'editor'])
@pytest.mark.parametrize('failure', ['audit', 'commit'])
async def test_required_failures_roll_back_real_client_and_audit_without_an_idempotency_service(unkeyed, mode, failure):
    p, c = unkeyed, unkeyed.c; before = snapshot(p)
    if failure == 'audit': c.events.create_event.side_effect = RuntimeError('private audit failure')
    else: p.flags.fail_commit = True
    with pytest.raises(ConstructionClientOperationError) as caught:
        await (unlink(p, idempotency_key=None) if mode == 'self' else edit(p, idempotency_key=None))
    assert snapshot(p) == before and c.session.rollback.await_count >= 1
    assert 'private' not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['self', 'editor'])
async def test_supplied_key_without_configured_replay_fails_before_business_service(unkeyed, mode):
    p = unkeyed; before = snapshot(p)
    with pytest.raises(ConstructionClientOperationError):
        await (unlink(p) if mode == 'self' else edit(p))
    assert snapshot(p) == before
    p.service.unlink_self.assert_not_called(); p.service.unlink_as_editor.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['self', 'editor'])
async def test_missing_fresh_canonical_identity_still_denies_without_mutation(unkeyed, mode):
    p = unkeyed; before = snapshot(p)
    p.c.identity.require_active_user.side_effect = ApiIdentityAccessError('inactive')
    with pytest.raises(ApiIdentityAccessError):
        await (unlink(p, idempotency_key=None) if mode == 'self' else edit(p, idempotency_key=None))
    assert snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['membership', 'grant'])
async def test_editor_needs_fresh_construction_authority_even_without_key(unkeyed, denial):
    p = unkeyed; before = snapshot(p)
    if denial == 'membership': p.c.access.has_active_platform_membership.return_value = False
    else: p.c.grant.status = 'revoked'
    with pytest.raises(ConstructionPermissionDeniedError): await edit(p, idempotency_key=None)
    assert snapshot(p) == before


def test_factory_without_encryption_key_does_not_build_replay_service(unkeyed, monkeypatch):
    p = unkeyed
    def forbidden(*args, **kwargs): raise AssertionError('Replay service is unnecessary without a key.')
    monkeypatch.setattr(module, 'build_api_idempotency_service', forbidden)
    platform = SimpleNamespace(execute=AsyncMock(), no_autoflush=nullcontext())
    workflow = module.build_construction_client_unlink_workflow(session=p.c.session, platform_session=platform)
    assert workflow.idempotency is None and workflow.service.identity.session is platform
    assert workflow.service.session is workflow.service.repository.session is p.c.session


def test_keyed_factory_keeps_existing_shared_encrypted_replay(unkeyed):
    p = unkeyed
    platform = SimpleNamespace(execute=AsyncMock(), no_autoflush=nullcontext())
    workflow = module.build_construction_client_unlink_workflow(session=p.c.session, platform_session=platform, encryption_key=p.key)
    assert workflow.idempotency.repository.session is p.c.session


@pytest.mark.parametrize('bad', ['missing_repository', 'foreign_session', 'missing_codec'])
def test_supplied_incomplete_idempotency_collaborator_is_rejected(unkeyed, bad):
    p = unkeyed
    collaborator = SimpleNamespace(repository=SimpleNamespace(session=p.c.session, reserve_record=AsyncMock()),
        response_codec=SimpleNamespace(decrypt=lambda data: data))
    if bad == 'missing_repository': collaborator.repository = None
    elif bad == 'foreign_session': collaborator.repository.session = object()
    else: collaborator.response_codec = None
    with pytest.raises(ValueError):
        module.ConstructionClientUnlinkWorkflow(session=p.c.session, unlink_service=p.service, idempotency_service=collaborator)
