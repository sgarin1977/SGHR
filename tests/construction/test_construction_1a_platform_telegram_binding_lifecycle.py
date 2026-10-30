from dataclasses import replace
from datetime import timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

from database.models import ApiIdempotencyRecord, EventLog, PlatformTelegramBindingToken as Token, UserAccount
from services.api_identity import ApiIdentityAccessError
from services.platform_telegram_binding_consume import (
    PlatformTelegramBindingOperationError as OperationError, PlatformTelegramBindingVersionConflictError as Conflict,
)
from services.platform_telegram_binding_lifecycle import build_platform_telegram_binding_lifecycle_service
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError as Denied
from test_construction_1a_platform_telegram_binding_consume import environment, proof, NOW, checkpoint
from test_construction_1a_platform_telegram_binding_workflow import workflow, counts, KEY


@pytest.fixture
def lifecycle(workflow):
    c = workflow
    c.service = build_platform_telegram_binding_lifecycle_service(session=c.env.bridge, encryption_key=KEY,
        now_provider=lambda: c.now)
    c.service.identity = c.identity
    return c


async def mutate(c, operation, **changes):
    options = dict(actor=c.actor, binding_id=c.env.binding_id, expected_row_version=1, trace_id='binding:lifecycle:test')
    options.update(changes)
    return await getattr(c.service, operation+'_binding')(**options)


@pytest.mark.asyncio
async def test_resend_revokes_original_and_creates_distinct_hash_only_token_with_one_audit(lifecycle):
    c = lifecycle
    original = c.env.session.get(Token, c.env.binding_id)
    original_hash = original.token_hash
    # SQLite's direct Session.get drops timezone metadata; the repository bridge
    # restores these test UTC values when it reloads the locked record.
    issued = original.issued_at.replace(tzinfo=timezone.utc)
    expires = original.expires_at.replace(tzinfo=timezone.utc)
    result = await mutate(c, 'resend')
    assert result.source_binding_id == c.env.binding_id and result.source_row_version == 2
    assert result.binding_id != result.source_binding_id and result.row_version == 1
    assert result.token != c.env.material.token and result.expires_at-result.issued_at == timedelta(minutes=15)
    assert result.user_id == c.actor.user_id and result.tenant_id == c.actor.tenant_id
    assert original.revoked_at == c.now and original.row_version == 2 and original.consumed_at is None
    assert (original.token_hash, original.issued_at, original.expires_at) == (original_hash, issued, expires)
    new = c.env.session.get(Token, result.binding_id)
    assert new.tenant_id == c.actor.tenant_id and new.initiated_by_user_id == c.actor.user_id
    assert new.consumed_at is new.revoked_at is None
    audit = c.env.session.execute(select(EventLog)).scalar_one()
    assert audit.event_type == 'platform_telegram_binding_resent' and audit.entity_id == result.binding_id
    assert audit.payload['source_binding_id'] == str(c.env.binding_id)
    assert audit.payload['new_binding_id'] == str(result.binding_id)
    assert audit.tenant_id == c.actor.tenant_id and audit.user_id == c.actor.user_id
    assert audit.trace_id == 'binding:lifecycle:test'
    serialized = repr(result)+json.dumps(audit.payload)
    for secret in (result.token, c.env.material.token, original_hash, new.token_hash): assert secret not in serialized
    assert counts(c) == (2, 0, 1, 0) and c.commits == 0 and c.env.session.in_transaction()
    c.env.session.rollback()
    checkpoint(c.env)


@pytest.mark.asyncio
async def test_after_caller_commit_old_secret_is_denied_and_new_secret_can_bind(lifecycle):
    c = lifecycle
    result = await mutate(c, 'resend')
    c.env.session.commit(); c.env.session.begin()
    with pytest.raises(Denied):
        await c.env.service.consume_token(token=c.env.material.token, telegram_init_data=proof())
    c.env.session.begin()
    consumed = await c.env.service.consume_token(token=result.token, telegram_init_data=proof())
    assert consumed.binding_id == result.binding_id and consumed.user_id == c.actor.user_id
    assert len(c.env.session.execute(select(UserAccount)).scalars().all()) == 1


@pytest.mark.asyncio
async def test_revoke_changes_only_unused_challenge_and_preserves_all_canonical_accounts(lifecycle):
    c = lifecycle
    for telegram_id in (123, 456):
        await c.env.service.accounts.create_or_get_account(user_id=c.actor.user_id, telegram_user_id=telegram_id)
    c.env.session.commit(); c.env.session.begin()
    before = [(row.id, row.platform_user_id) for row in c.env.session.execute(select(UserAccount)).scalars().all()]
    result = await mutate(c, 'revoke')
    assert result.binding_id == c.env.binding_id and result.row_version == 2 and result.revoked_at == c.now
    assert before == [(row.id, row.platform_user_id) for row in c.env.session.execute(select(UserAccount)).scalars().all()]
    audit = c.env.session.execute(select(EventLog)).scalar_one()
    assert audit.event_type == 'platform_telegram_binding_revoked'
    assert counts(c) == (1, 2, 1, 0) and c.commits == 0
    c.env.session.rollback()
    checkpoint(c.env, account_count=2)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
@pytest.mark.parametrize('fault', ['foreign_user', 'foreign_tenant', 'missing', 'blocked', 'membership'])
async def test_lifecycle_requires_platform_actor_and_own_saved_tenant_context(lifecycle, operation, fault):
    c = lifecycle
    options = {}
    if fault in ('foreign_user', 'foreign_tenant'):
        c.actor = replace(c.actor, **({'user_id': c.env.other} if fault == 'foreign_user' else {'tenant_id': uuid4()}))
        c.identity.require_actor.return_value = c.actor
    if fault == 'missing': options['binding_id'] = uuid4()
    if fault == 'blocked':
        c.env.session.execute(update(c.env.users).where(c.env.users.c.id == c.env.owner).values(status='blocked'))
        c.env.session.commit(); c.env.session.begin()
    if fault == 'membership': c.identity.require_actor.side_effect = ApiIdentityAccessError('No membership.')
    with pytest.raises((Denied, ApiIdentityAccessError)): await mutate(c, operation, **options)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
@pytest.mark.parametrize('state', ['expired', 'future', 'consumed', 'revoked'])
async def test_only_live_unused_token_can_be_revoked_or_replaced(lifecycle, operation, state):
    c = lifecycle
    version = 1
    if state == 'expired': c.now = NOW+timedelta(minutes=15)
    if state == 'future': c.now = NOW-timedelta(seconds=1)
    if state in ('consumed', 'revoked'):
        c.env.session.execute(update(c.env.token_table).values(**{state+'_at': NOW, 'row_version': 2}))
        c.env.session.commit(); c.env.session.begin(); version = 2
    with pytest.raises(Denied): await mutate(c, operation, expected_row_version=version)
    assert counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
@pytest.mark.parametrize('version', [None, 0, -1, True, '1', 2**63-1])
async def test_expected_version_is_required_and_validated_before_database_access(lifecycle, operation, version):
    c = lifecycle
    with pytest.raises(ValueError): await mutate(c, operation, expected_row_version=version)
    assert c.env.bridge.statements == [] and counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
async def test_stale_version_and_conditional_write_miss_do_not_add_token_or_audit(lifecycle, operation):
    c = lifecycle
    with pytest.raises(Conflict): await mutate(c, operation, expected_row_version=2)
    c.env.session.begin()
    c.service.tokens.revoke_token = AsyncMock(return_value=None)
    with pytest.raises(Conflict): await mutate(c, operation)
    checkpoint(c.env)
    assert c.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
@pytest.mark.parametrize('fault', ['before_flush', 'after_flush', 'bad_receipt'])
async def test_mandatory_audit_failure_rolls_back_revocation_and_any_replacement(lifecycle, operation, fault):
    c = lifecycle
    original = c.service.events.create_event
    async def failing(**kwargs):
        if fault == 'before_flush': raise RuntimeError('Simulated audit failure.')
        await original(**kwargs)
        assert c.env.session.execute(select(EventLog.id)).first() is not None
        if fault == 'after_flush': raise RuntimeError('Simulated failure after audit flush.')
        return SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    c.service.events.create_event = failing
    with pytest.raises((OperationError, RuntimeError)): await mutate(c, operation)
    checkpoint(c.env)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


@pytest.mark.asyncio
async def test_failed_replacement_insert_rolls_back_original_token_revocation(lifecycle):
    c = lifecycle
    original = c.service.tokens.create_token
    async def failing(**kwargs):
        await original(**kwargs)
        assert len(c.env.session.execute(select(Token.id)).all()) == 2
        raise RuntimeError('Simulated failure after replacement insert.')
    c.service.tokens.create_token = failing
    with pytest.raises(RuntimeError): await mutate(c, 'resend')
    checkpoint(c.env)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['resend', 'revoke'])
async def test_membership_revoked_after_audit_flush_rolls_back_entire_operation(lifecycle, operation):
    c = lifecycle
    c.identity.require_actor.side_effect = [c.actor, c.actor, ApiIdentityAccessError('Revoked membership.')]
    with pytest.raises(ApiIdentityAccessError): await mutate(c, operation)
    checkpoint(c.env)


@pytest.mark.asyncio
async def test_old_version_cannot_create_second_replacement_after_first_committed_resend(lifecycle):
    c = lifecycle
    first = await mutate(c, 'resend')
    c.env.session.commit(); c.env.session.begin()
    with pytest.raises(Conflict): await mutate(c, 'resend')
    assert counts(c) == (2, 0, 1, 0)
    assert c.env.session.get(Token, first.binding_id).revoked_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['tenant_id', 'user_id', 'initiated_by_user_id', 'token_hash'])
async def test_revoke_receipt_cannot_change_stored_challenge_context(lifecycle, field):
    c = lifecycle
    original = c.service.tokens.revoke_token
    async def corrupt(**kwargs):
        row = await original(**kwargs)
        set_committed_value(row, field, '0'*64 if field == 'token_hash' else uuid4())
        return row
    c.service.tokens.revoke_token = corrupt
    with pytest.raises(OperationError): await mutate(c, 'resend')
    checkpoint(c.env)
