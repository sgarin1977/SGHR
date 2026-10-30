from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update

from database.models import ConstructionClient, ConstructionClientUserLinkRequest as Request
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_client_self_link import ConstructionClientSelfLinkService as Service
from services.construction.construction_client_self_link_workflow import (
    ConstructionClientSelfLinkWorkflow as Workflow, build_construction_client_self_link_workflow, SELF_LINK,
)
from services.construction.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction.construction_clients import ConstructionClientOperationError, ConstructionClientValidationError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation, seed_request, NOW
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db


@pytest.fixture
async def self_pipeline(pipeline):
    p, c = pipeline, pipeline.c
    c.self_actor = ApiActorContext(c.operator, c.tenant, None, (), 'ru', None, 'active')
    c.self_context = ConstructionAccessContext(c.operator, c.tenant, ('OWNER',), ('construction.projects.edit',),
        (ConstructionGrantContext('OWNER', 'tenant', c.tenant),))
    p.identity = SimpleNamespace(session=SimpleNamespace(), require_active_actor=AsyncMock(return_value=c.self_actor))
    p.service = Service(session=c.session, repository=c.clients, access_repository=c.access,
        event_repository=c.events, identity_reader=p.identity, now_provider=lambda: c.now)
    p.service.link_self = AsyncMock(wraps=p.service.link_self)
    p.service.replace_with_self = AsyncMock(wraps=p.service.replace_with_self)
    p.workflow = Workflow(session=c.session, self_link_service=p.service,
        idempotency_service=p.idempotency, now_provider=lambda: c.now)
    c.session.commit.reset_mock()
    c.session.rollback.reset_mock()
    return p


async def link(p, **changes):
    c = p.c
    if not c.sync.in_transaction():
        c.sync.begin()
    kw = dict(actor=c.self_actor, access_context=c.self_context, client_id=c.client,
        expected_row_version=1, idempotency_key='self-link-key', trace_id='self-link-workflow')
    kw.update(changes)
    return await p.workflow.link_self(**kw)


async def replace_link(p, **changes):
    c = p.c
    if not c.sync.in_transaction():
        c.sync.begin()
    kw = dict(actor=c.self_actor, access_context=c.self_context, client_id=c.client,
        expected_row_version=1, previous_user_id=c.target,
        idempotency_key='self-link-key', trace_id='replace-with-self-workflow')
    kw.update(changes)
    return await p.workflow.replace_with_self(**kw)


def snapshot(p):
    c = p.c
    return (c.sync.execute(select(ConstructionClient.__table__).order_by(ConstructionClient.id)).all(),
        c.sync.execute(select(Request.__table__).order_by(Request.id)).all(),
        c.sync.execute(text('SELECT * FROM users ORDER BY id')).all(),
        c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one(),
        c.sync.execute(text('SELECT data FROM client_link_replay_ledger ORDER BY id')).all())


def change_client(p, **values):
    c = p.c
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id == c.client).values(**values))
    c.sync.commit()


@pytest.mark.asyncio
async def test_self_link_commits_actual_client_audit_and_encrypted_receipt_without_changing_requests_or_users(self_pipeline):
    p, c = self_pipeline, self_pipeline.c
    requests = c.sync.execute(select(Request.__table__)).all()
    users = c.sync.execute(text('SELECT * FROM users ORDER BY id')).all()
    async def membership(**kw):
        assert c.tenant_bindings and c.tenant_bindings[-1] == str(c.tenant)
        return True
    c.access.has_active_platform_membership.side_effect = membership
    result = await link(p)
    assert result.changed and result.linked_user_id == c.operator and result.row_version == 2
    assert c.sync.execute(select(ConstructionClient.linked_user_id).where(ConstructionClient.id == c.client)).scalar_one() == c.operator
    assert c.sync.execute(select(Request.__table__)).all() == requests
    assert c.sync.execute(text('SELECT * FROM users ORDER BY id')).all() == users
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one() == 1
    c.session.commit.assert_awaited_once()
    row, = p.ledger.all()
    assert row.operation == SELF_LINK and row.principal_id == c.operator and row.response_status == 200
    saved = p.codec.decrypt(row.response_ciphertext)
    assert saved['binding']['tenant_id'] == str(c.tenant) and saved['data']['linked_user_id'] == str(c.operator)
    assert 'token' not in json.dumps(saved) and 'recipient_context' not in json.dumps(saved)
    assert c.audit_rows[-1].payload['related_client_id'] == str(c.client)
    assert c.audit_rows[-1].payload['identity_link_action'] == 'self_link'


@pytest.mark.asyncio
@pytest.mark.parametrize('later', ['unchanged', 'unlinked', 'other_user', 'archived'])
async def test_replay_returns_original_success_without_relinking_or_changing_later_state(self_pipeline, later):
    p, c = self_pipeline, self_pipeline.c
    first = await link(p)
    if later == 'unlinked':
        change_client(p, linked_user_id=None, row_version=3)
    elif later == 'other_user':
        change_client(p, linked_user_id=c.target, row_version=3)
    elif later == 'archived':
        change_client(p, deleted_at=NOW + timedelta(seconds=2), row_version=3)
    before = snapshot(p)
    assert await link(p, trace_id='lost-response-retry') == first
    assert snapshot(p) == before and p.service.link_self.await_count == 1
    assert c.session.commit.await_count == 2


@pytest.mark.asyncio
async def test_explicit_replacement_commits_once_and_replay_does_not_replace_later_identity(self_pipeline):
    p, c = self_pipeline, self_pipeline.c
    change_client(p, linked_user_id=c.target)
    first = await replace_link(p)
    assert first.changed and first.linked_user_id == c.operator and first.row_version == 2
    event = c.audit_rows[-1]
    assert event.payload['identity_link_action'] == 'replace_with_self'
    assert event.payload['changes'][0] == {'field': 'linked_user_id', 'old_value': str(c.target), 'new_value': str(c.operator)}
    change_client(p, linked_user_id=c.foreign_user, row_version=3)
    before = snapshot(p)
    assert await replace_link(p) == first and snapshot(p) == before
    assert p.service.replace_with_self.await_count == 1


@pytest.mark.asyncio
async def test_matching_noop_has_no_audit_or_version_change_and_replay_never_relinks(self_pipeline):
    p, c = self_pipeline, self_pipeline.c
    change_client(p, linked_user_id=c.operator, row_version=5)
    first = await link(p, expected_row_version=5)
    assert not first.changed and first.row_version == 5 and first.audit_id is None
    assert c.sync.execute(text('SELECT count(*) FROM request_repo_test_audit')).scalar_one() == 0
    change_client(p, linked_user_id=None, row_version=6)
    before = snapshot(p)
    assert await link(p, expected_row_version=5) == first and snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['identity', 'identity_changed', 'membership', 'revoked', 'scope'])
async def test_even_original_self_receipt_requires_fresh_identity_membership_and_edit_scope(self_pipeline, denial):
    p, c = self_pipeline, self_pipeline.c
    await link(p)
    before = snapshot(p)
    if denial == 'identity':
        p.identity.require_active_actor.side_effect = ApiIdentityAccessError('inactive')
    elif denial == 'identity_changed':
        p.identity.require_active_actor.return_value = replace(c.self_actor, user_id=c.target)
    elif denial == 'membership':
        c.access.has_active_platform_membership.return_value = False
    elif denial == 'revoked':
        c.grant.status = 'revoked'
    else:
        c.grant.scope_type, c.grant.scope_id = 'project', uuid4()
    expected = ApiIdentityAccessError if denial.startswith('identity') else (ConstructionPermissionDeniedError, ConstructionScopeDeniedError)
    with pytest.raises(expected):
        await link(p)
    assert snapshot(p) == before and p.service.link_self.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['client', 'version', 'replacement', 'previous_identity'])
async def test_same_key_cannot_change_client_version_or_explicit_replacement_semantics(self_pipeline, change):
    p, c = self_pipeline, self_pipeline.c
    if change == 'previous_identity':
        change_client(p, linked_user_id=c.target)
        await replace_link(p)
    else:
        await link(p)
    before = snapshot(p)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        if change == 'client':
            await link(p, client_id=c.other_client)
        elif change == 'version':
            await link(p, expected_row_version=2)
        elif change == 'replacement':
            await replace_link(p)
        else:
            await replace_link(p, previous_user_id=c.foreign_user)
    assert snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['complete', 'commit', 'wrong_completion_id', 'audit', 'final_authority', 'cancel_complete'])
async def test_actual_client_audit_and_reservation_roll_back_together_on_required_failure(self_pipeline, failure):
    p, c = self_pipeline, self_pipeline.c
    before = snapshot(p)
    if failure == 'complete':
        p.flags.fail_complete = True
    elif failure == 'commit':
        p.flags.fail_commit = True
    elif failure == 'wrong_completion_id':
        p.flags.corrupt_complete = True
    elif failure == 'cancel_complete':
        p.flags.cancel_complete = True
    elif failure == 'audit':
        c.events.create_event.side_effect = RuntimeError('private audit details')
    else:
        original = p.ledger.complete_record
        async def complete(**kw):
            row = await original(**kw)
            c.access.has_active_platform_membership.return_value = False
            return row
        p.ledger.complete_record = complete
    import asyncio
    expected = asyncio.CancelledError if failure == 'cancel_complete' else ConstructionPermissionDeniedError if failure == 'final_authority' else ConstructionClientOperationError
    with pytest.raises(expected) as caught:
        await link(p)
    assert 'private' not in str(caught.value)
    assert snapshot(p) == before
    assert c.session.rollback.await_count >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['stale', 'implicit_overwrite', 'wrong_previous'])
async def test_version_and_explicit_replacement_guards_roll_back_new_reservation(self_pipeline, invalid):
    p, c = self_pipeline, self_pipeline.c
    change_client(p, linked_user_id=c.target, row_version=2)
    before = snapshot(p)
    expected = ConstructionClientValidationError if invalid == 'implicit_overwrite' else ConstructionRowVersionConflictError
    with pytest.raises(expected):
        if invalid == 'wrong_previous':
            await replace_link(p, expected_row_version=2, previous_user_id=c.foreign_user)
        else:
            await link(p, expected_row_version=1 if invalid == 'stale' else 2)
    assert snapshot(p) == before


@pytest.mark.asyncio
async def test_without_key_version_checks_and_matching_noop_remain_effective(self_pipeline):
    p = self_pipeline
    first = await link(p, idempotency_key=None)
    assert first.changed and p.ledger.calls == 0
    before = snapshot(p)
    with pytest.raises(ConstructionRowVersionConflictError):
        await link(p, idempotency_key=None)
    assert snapshot(p) == before
    result = await link(p, idempotency_key=None, expected_row_version=2)
    assert not result.changed and result.audit_id is None and result.row_version == 2
    assert not p.ledger.all()


@pytest.mark.asyncio
async def test_expired_receipt_cannot_reapply_old_version_to_a_later_client(self_pipeline):
    p, c = self_pipeline, self_pipeline.c
    await link(p)
    change_client(p, linked_user_id=None, row_version=3)
    before = snapshot(p)
    c.now = NOW + timedelta(hours=25)
    with pytest.raises(ConstructionRowVersionConflictError):
        await link(p)
    assert snapshot(p) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['target', 'version', 'binding', 'changed_flag', 'audit'])
async def test_invalid_encrypted_receipt_is_rejected_without_live_link_or_mutation(self_pipeline, bad):
    p = self_pipeline
    await link(p)
    row, = p.ledger.all()
    saved = p.codec.decrypt(row.response_ciphertext)
    if bad == 'target':
        saved['data']['linked_user_id'] = str(p.c.target)
    elif bad == 'version':
        saved['data']['row_version'] = 900
    elif bad == 'binding':
        saved['binding']['principal_id'] = str(p.c.target)
    elif bad == 'changed_flag':
        saved['data']['changed'] = 1
    else:
        saved['data']['audit_id'] = None
    row.response_ciphertext = p.codec.encrypt(saved)
    p.ledger.save(row)
    p.c.sync.commit()
    before = snapshot(p)
    with pytest.raises(ConstructionClientOperationError):
        await link(p)
    assert snapshot(p) == before and p.service.link_self.await_count == 1


@pytest.mark.asyncio
async def test_processing_record_returns_conflict_without_second_link(self_pipeline):
    p = self_pipeline
    await link(p)
    row, = p.ledger.all()
    row.status = 'processing'
    row.response_status = row.response_ciphertext = None
    p.ledger.save(row)
    p.c.sync.commit()
    before = snapshot(p)
    with pytest.raises(ConstructionClientLinkIdempotencyInProgressError):
        await link(p)
    assert snapshot(p) == before and p.service.link_self.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'expected_row_version': True}, {'expected_row_version': 0},
    {'client_id': 'client'}, {'idempotency_key': ''}, {'idempotency_key': 'x' * 256}, {'trace_id': ' '}])
async def test_invalid_request_does_not_reserve_or_link(self_pipeline, changes):
    p = self_pipeline
    with pytest.raises(ConstructionClientValidationError):
        await link(p, **changes)
    assert p.ledger.calls == 0 and p.service.link_self.await_count == 0


def test_factory_preserves_shared_writes_and_separate_canonical_identity_connection(self_pipeline):
    p, c = self_pipeline, self_pipeline.c
    platform = SimpleNamespace(execute=AsyncMock(), no_autoflush=nullcontext())
    workflow = build_construction_client_self_link_workflow(session=c.session, platform_session=platform, encryption_key=p.key)
    assert workflow.service.session is workflow.idempotency.repository.session is c.session
    assert workflow.service.identity.session is platform
