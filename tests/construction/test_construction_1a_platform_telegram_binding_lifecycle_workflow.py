from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from database.models import ApiIdempotencyRecord, EventLog, PlatformTelegramBindingToken as Token
from services.api_identity import ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.platform_telegram_binding_consume import PlatformTelegramBindingOperationError as OperationError, PlatformTelegramBindingVersionConflictError as Conflict
from services.platform_telegram_binding_lifecycle import PlatformTelegramBindingLifecycleService
from services.platform_telegram_binding_lifecycle_workflow import (
    PlatformTelegramBindingLifecycleWorkflow, build_platform_telegram_binding_lifecycle_workflow, RESEND, REVOKE,
)
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError as Denied
from test_construction_1a_platform_telegram_binding_consume import environment, proof, checkpoint
from test_construction_1a_platform_telegram_binding_workflow import workflow, counts, consume, KEY


@pytest.fixture
def lifecycle_workflow(workflow):
    c = workflow
    core = PlatformTelegramBindingLifecycleService(session=c.env.bridge, token_repository=c.workflow.tokens,
        event_repository=c.workflow.events, identity_service=c.identity,
        token_deriver=c.workflow.deriver, now_provider=lambda: c.now)
    c.lifecycle = PlatformTelegramBindingLifecycleWorkflow(binding_workflow=c.workflow, lifecycle_service=core)
    return c


async def mutate(c, operation='resend', key='lifecycle-key', **changes):
    if not c.env.session.in_transaction(): c.env.session.begin()
    options = dict(actor=c.actor, binding_id=c.env.binding_id, expected_row_version=1, idempotency_key=key)
    options.update(changes)
    return await getattr(c.lifecycle, operation+'_binding')(**options)


@pytest.mark.asyncio
async def test_resend_commits_single_audit_replacement_and_secret_free_snapshot(lifecycle_workflow):
    c = lifecycle_workflow
    result = await mutate(c)
    assert counts(c) == (2, 0, 1, 1) and c.commits == 1
    assert result.source_binding_id == c.env.binding_id and result.binding_id != c.env.binding_id
    source, replacement = c.env.session.get(Token, c.env.binding_id), c.env.session.get(Token, result.binding_id)
    assert source.revoked_at is not None and source.row_version == 2
    assert replacement.revoked_at is replacement.consumed_at is None
    audit = c.env.session.execute(select(EventLog)).scalar_one()
    record = c.env.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    assert record.operation == RESEND and record.status == 'completed'
    assert record.response_status == 200 and record.tenant_id == c.actor.tenant_id
    snapshot = c.workflow.idempotency.response_codec.decrypt(record.response_ciphertext)
    text = json.dumps(snapshot)+json.dumps(audit.payload)+repr(result)
    assert all(secret not in text for secret in (result.token, replacement.token_hash, source.token_hash, c.env.material.token))
    assert set(snapshot['data']) == {'source_binding_id', 'source_row_version', 'source_revoked_at',
        'binding_id', 'user_id', 'tenant_id', 'row_version', 'issued_at', 'expires_at'}


@pytest.mark.asyncio
async def test_resend_same_key_returns_same_secret_after_expiry_without_new_writes(lifecycle_workflow):
    c = lifecycle_workflow
    first = await mutate(c)
    before = counts(c)
    c.now += timedelta(minutes=20)
    repeated = await mutate(c)
    assert first == repeated and first.token == repeated.token and repeated.expires_at < c.now
    assert counts(c) == before and c.commits == 2


@pytest.mark.asyncio
async def test_resend_replay_after_replacement_consumed_returns_original_receipt(lifecycle_workflow):
    c = lifecycle_workflow
    first = await mutate(c)
    await consume(c, first.token)
    before = counts(c)
    repeated = await mutate(c)
    assert repeated == first and repeated.row_version == 1
    assert c.env.session.get(Token, first.binding_id).row_version == 2
    assert counts(c) == before


@pytest.mark.asyncio
async def test_same_key_with_changed_source_or_expected_version_is_conflict(lifecycle_workflow):
    c = lifecycle_workflow
    first = await mutate(c)
    before = counts(c)
    for changes in (dict(expected_row_version=2), dict(binding_id=first.binding_id)):
        with pytest.raises(ApiIdempotencyKeyReusedError): await mutate(c, **changes)
        assert counts(c) == before


@pytest.mark.asyncio
async def test_new_key_with_stale_source_version_cannot_create_another_replacement(lifecycle_workflow):
    c = lifecycle_workflow
    await mutate(c)
    before = counts(c)
    with pytest.raises(Conflict): await mutate(c, key='second-key')
    assert counts(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [None, '', ' ', 'x'*256, 1])
async def test_secret_issuance_requires_idempotency_key_before_writes(lifecycle_workflow, key):
    c = lifecycle_workflow
    with pytest.raises(ValueError): await mutate(c, key=key)
    checkpoint(c.env)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('key', [None, 'revoke-key'])
async def test_versioned_revoke_commits_with_or_without_optional_key(lifecycle_workflow, key):
    c = lifecycle_workflow
    result = await mutate(c, 'revoke', key=key)
    assert result.row_version == 2 and result.revoked_at == c.now
    assert counts(c) == (1, 0, 1, int(key is not None)) and c.commits == 1
    if key is None:
        with pytest.raises(Conflict): await mutate(c, 'revoke', key=None)
    else:
        before = counts(c)
        assert await mutate(c, 'revoke', key=key) == result
        assert counts(c) == before and c.commits == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
async def test_lost_authorization_denies_replay_without_new_audit(lifecycle_workflow, operation):
    c = lifecycle_workflow
    await mutate(c, operation)
    before = counts(c)
    c.identity.require_actor.side_effect = ApiIdentityAccessError('Membership revoked.')
    with pytest.raises(ApiIdentityAccessError): await mutate(c, operation)
    assert counts(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
@pytest.mark.parametrize('fault', ['audit', 'completion', 'commit', 'authorization'])
async def test_workflow_failure_rolls_back_token_audit_and_reservation_together(lifecycle_workflow, operation, fault):
    c = lifecycle_workflow
    if fault == 'audit':
        original = c.lifecycle.service.events.create_event
        async def fail(**kwargs):
            await original(**kwargs)
            raise RuntimeError('Failure after real audit flush.')
        c.lifecycle.service.events.create_event = fail
    elif fault == 'completion':
        original = c.workflow.idempotency.complete
        async def fail(**kwargs):
            await original(**kwargs)
            raise RuntimeError('Failure after response completion.')
        c.workflow.idempotency.complete = fail
    elif fault == 'commit':
        async def fail(): raise RuntimeError('Commit failed.')
        c.env.bridge.commit = fail
    else:
        original = c.workflow.idempotency.complete
        async def fail(**kwargs):
            record = await original(**kwargs)
            c.identity.require_actor.side_effect = ApiIdentityAccessError('Membership revoked after completion.')
            return record
        c.workflow.idempotency.complete = fail
    with pytest.raises((RuntimeError, ApiIdentityAccessError)): await mutate(c, operation)
    checkpoint(c.env)
    assert counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
@pytest.mark.parametrize('fault', ['binding', 'owner', 'source_version', 'extra_secret', 'resource'])
async def test_corrupted_snapshot_fails_closed_without_regeneration_or_writes(lifecycle_workflow, operation, fault):
    c = lifecycle_workflow
    await mutate(c, operation)
    record = c.env.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    snapshot = c.workflow.idempotency.response_codec.decrypt(record.response_ciphertext)
    if fault == 'binding': snapshot['binding']['tenant_id'] = str(uuid4())
    if fault == 'owner': snapshot['data']['user_id'] = str(uuid4())
    if fault == 'source_version': snapshot['data']['source_row_version' if operation == 'resend' else 'row_version'] = True
    if fault == 'extra_secret': snapshot['data']['token'] = 'private-unsupported-secret'
    if fault == 'resource': snapshot['data']['binding_id'] = str(uuid4())
    record.response_ciphertext = c.workflow.idempotency.response_codec.encrypt(snapshot)
    c.env.session.commit()
    before = counts(c)
    with pytest.raises(OperationError): await mutate(c, operation)
    assert counts(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
async def test_foreign_tenant_source_cannot_be_revoked_or_reissued(lifecycle_workflow, operation):
    c = lifecycle_workflow
    tenant = uuid4()
    tenants = c.env.token_table.metadata.tables['tenants']
    c.env.session.execute(insert(tenants).values(id=tenant))
    c.env.session.commit(); c.env.session.begin()
    c.actor = replace(c.actor, tenant_id=tenant)
    c.identity.require_actor.return_value = c.actor
    # Even a currently authorized session in another real organization cannot
    # mutate a token initiated in the original saved organization context.
    with pytest.raises(Denied): await mutate(c, operation)
    checkpoint(c.env)
    assert counts(c) == (1, 0, 0, 0)


def test_factory_shares_repositories_identity_deriver_and_session(lifecycle_workflow):
    c = lifecycle_workflow
    built = build_platform_telegram_binding_lifecycle_workflow(session=c.env.bridge,
        encryption_key=KEY, telegram_verifier=c.env.service.verifier, now_provider=lambda: c.now)
    assert built.session is built.base.session is built.service.session is c.env.bridge
    for name in ('tokens', 'events', 'identity', 'deriver'):
        assert getattr(built.base, name) is getattr(built.service, name)
    other = PlatformTelegramBindingLifecycleService(session=c.env.bridge, token_repository=built.base.tokens,
        event_repository=built.base.events, identity_service=c.identity, token_deriver=built.base.deriver)
    with pytest.raises(ValueError): PlatformTelegramBindingLifecycleWorkflow(binding_workflow=built.base, lifecycle_service=other)
