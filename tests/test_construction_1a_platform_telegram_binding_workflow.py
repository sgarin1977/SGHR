from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

from api.telegram_auth import TelegramAuthenticationError
from database.models import ApiIdempotencyRecord, EventLog, PlatformTelegramBindingToken as Token, UserAccount
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.platform_telegram_binding_consume import PlatformTelegramBindingOperationError as OperationError
from services.platform_telegram_binding_replay import PlatformTelegramBindingTokenDeriver
from services.platform_telegram_binding_tokens import PlatformTelegramBindingTokenDeniedError as Denied
from services.platform_telegram_binding_workflow import (
    INITIATE, CONSUME, PlatformTelegramBindingIdempotencyInProgressError as InProgress,
    build_platform_telegram_binding_workflow,
)
from test_construction_1a_platform_telegram_binding_consume import environment, proof, NOW, Result


KEY = Fernet.generate_key().decode('ascii')


@pytest.fixture
def workflow(environment):
    env = environment
    table = ApiIdempotencyRecord.__table__.to_metadata(env.token_table.metadata)
    table.create(env.session.bind)
    original_execute = env.bridge.execute
    class WorkflowResult(Result):
        def scalar_one_or_none(self):
            row = super().scalar_one_or_none()
            if isinstance(row, ApiIdempotencyRecord):
                for name in ('created_at', 'completed_at', 'expires_at'):
                    value = getattr(row, name)
                    if value is not None and value.tzinfo is None:
                        set_committed_value(row, name, value.replace(tzinfo=timezone.utc))
            return row
    async def execute(statement):
        wrapped = await original_execute(statement)
        return WorkflowResult(wrapped.result)
    env.bridge.execute = execute
    c = SimpleNamespace(env=env, now=NOW+timedelta(minutes=1), commits=0)
    async def commit():
        c.commits += 1
        env.session.commit()
    env.bridge.commit = commit
    c.actor = ApiActorContext(user_id=env.owner, tenant_id=env.tenant, active_role='client', roles=('client',),
        language_code='ru', timezone=None, status='active')
    c.workflow = build_platform_telegram_binding_workflow(session=env.bridge,
        encryption_key=KEY, telegram_verifier=env.service.verifier, now_provider=lambda: c.now)
    c.identity = SimpleNamespace(session=env.bridge, require_actor=AsyncMock(return_value=c.actor))
    c.workflow.identity = c.identity
    return c


async def initiate(c, key='init-key', **kwargs):
    if not c.env.session.in_transaction(): c.env.session.begin()
    return await c.workflow.initiate_binding(actor=c.actor, idempotency_key=key, **kwargs)


async def consume(c, token, key='consume-key', telegram_id=123456789, **kwargs):
    if not c.env.session.in_transaction(): c.env.session.begin()
    return await c.workflow.consume_binding(token=token, idempotency_key=key,
        telegram_init_data=proof(telegram_id=telegram_id, as_of=c.now), **kwargs)


def counts(c):
    return tuple(len(c.env.session.execute(select(model)).scalars().all())
        for model in (Token, UserAccount, EventLog, ApiIdempotencyRecord))


@pytest.mark.asyncio
async def test_initiation_commits_token_audit_and_idempotency_together_without_secret_snapshot(workflow):
    c = workflow
    result = await initiate(c)
    assert counts(c) == (2, 0, 1, 1) and c.commits == 1
    row = c.env.session.get(Token, result.binding_id)
    assert (row.user_id, row.tenant_id, row.initiated_by_user_id) == (c.actor.user_id, c.actor.tenant_id, c.actor.user_id)
    assert result.expires_at-result.issued_at == timedelta(minutes=15)
    audit = c.env.session.execute(select(EventLog)).scalar_one()
    record = c.env.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    snapshot = c.workflow.idempotency.response_codec.decrypt(record.response_ciphertext)
    assert record.operation == INITIATE and record.status == 'completed' and record.response_status == 201
    assert audit.event_type == 'platform_telegram_binding_initiated' and audit.entity_id == result.binding_id
    assert record.tenant_id == audit.tenant_id == c.actor.tenant_id
    serialized = json.dumps(snapshot)+json.dumps(audit.payload)+repr(result)
    assert result.token not in serialized and row.token_hash not in serialized
    assert set(snapshot['data']) == {'binding_id', 'user_id', 'tenant_id', 'issued_at', 'expires_at', 'row_version'}


@pytest.mark.asyncio
async def test_initiation_replay_restores_same_secret_without_new_token_or_audit(workflow):
    c = workflow
    first = await initiate(c)
    before = counts(c)
    c.now += timedelta(minutes=20)  # Replaying must not silently extend the token TTL.
    repeated = await initiate(c)
    assert repeated == first and repeated.token == first.token and counts(c) == before
    assert repeated.expires_at < c.now
    assert c.commits == 2
    c.identity.require_actor.assert_awaited()


@pytest.mark.asyncio
async def test_consume_has_no_platform_session_and_replay_returns_original_success_after_token_used(workflow):
    c = workflow
    material = await initiate(c)
    c.identity.require_actor.side_effect = AssertionError('Consume must not require a Platform actor.')
    first = await consume(c, material.token)
    assert first.account_created and first.user_id == c.actor.user_id and first.tenant_id == c.actor.tenant_id
    before = counts(c)
    assert before == (2, 1, 2, 2)
    c.now += timedelta(minutes=20)
    replay = await consume(c, material.token)
    assert replay == first and counts(c) == before
    assert c.commits == 3


@pytest.mark.asyncio
async def test_same_key_with_different_verified_telegram_identity_is_conflict(workflow):
    c = workflow
    first = await consume(c, c.env.material.token)
    before = counts(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await consume(c, c.env.material.token, telegram_id=456789)
    assert counts(c) == before
    assert c.env.session.execute(select(UserAccount)).scalar_one().id == first.account_id


@pytest.mark.asyncio
async def test_same_key_with_another_binding_token_is_conflict_in_saved_owner_context(workflow):
    c = workflow
    material = await initiate(c)
    await consume(c, c.env.material.token)
    before = counts(c)
    with pytest.raises(ApiIdempotencyKeyReusedError): await consume(c, material.token)
    assert counts(c) == before
    assert c.env.session.get(Token, material.binding_id).consumed_at is None


@pytest.mark.asyncio
async def test_new_key_cannot_consume_already_used_token(workflow):
    c = workflow
    await consume(c, c.env.material.token)
    before = counts(c)
    with pytest.raises(Denied): await consume(c, c.env.material.token, key='different-key')
    assert counts(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['initiate', 'consume'])
@pytest.mark.parametrize('key', [None, '', ' ', 'x'*256, 123])
async def test_idempotency_key_is_mandatory_before_any_binding_write(workflow, method, key):
    c = workflow
    with pytest.raises(ValueError):
        if method == 'initiate': await initiate(c, key=key)
        else: await consume(c, c.env.material.token, key=key)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['initiate', 'consume'])
@pytest.mark.parametrize('fault', ['after_flush', 'bad_receipt'])
async def test_audit_failure_rolls_back_business_changes_and_processing_reservation(workflow, method, fault):
    c = workflow
    original = c.workflow.events.create_event
    async def failing(**kwargs):
        event = await original(**kwargs)
        assert c.env.session.execute(select(EventLog.id)).first() is not None
        if fault == 'after_flush': raise RuntimeError('Simulated audit failure after real flush.')
        return SimpleNamespace(id=event.id, tenant_id=uuid4())
    c.workflow.events.create_event = failing
    with pytest.raises((RuntimeError, OperationError, AttributeError)):
        if method == 'initiate': await initiate(c)
        else: await consume(c, c.env.material.token)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0
    assert c.env.session.get(Token, c.env.binding_id).consumed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['initiate', 'consume'])
async def test_idempotency_completion_failure_rolls_back_real_business_and_audit_writes(workflow, method):
    c = workflow
    original = c.workflow.idempotency.complete
    async def failing(**kwargs):
        await original(**kwargs)
        assert c.env.session.execute(select(EventLog.id)).first() is not None
        raise RuntimeError('Simulated completion failure.')
    c.workflow.idempotency.complete = failing
    with pytest.raises(RuntimeError):
        if method == 'initiate': await initiate(c)
        else: await consume(c, c.env.material.token)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


@pytest.mark.asyncio
async def test_lost_membership_after_initiation_writes_rolls_back_token_audit_and_snapshot(workflow):
    c = workflow
    c.identity.require_actor.side_effect = [c.actor, c.actor, ApiIdentityAccessError('Revoked membership.')]
    with pytest.raises(ApiIdentityAccessError): await initiate(c)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


@pytest.mark.asyncio
async def test_replay_requires_current_platform_authorization_for_initiation(workflow):
    c = workflow
    await initiate(c)
    before = counts(c)
    c.identity.require_actor.side_effect = ApiIdentityAccessError('Revoked membership.')
    with pytest.raises(ApiIdentityAccessError): await initiate(c)
    assert counts(c) == before and c.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['actor_type', 'inactive', 'missing_tenant', 'foreign_identity'])
async def test_initiation_authorizes_canonical_user_and_selected_tenant_before_writes(workflow, fault):
    c = workflow
    if fault == 'actor_type': c.actor = SimpleNamespace(user_id=c.actor.user_id, tenant_id=c.actor.tenant_id, status='active')
    if fault == 'inactive': c.actor = replace(c.actor, status='blocked')
    if fault == 'missing_tenant': c.actor = replace(c.actor, tenant_id=None)
    if fault == 'foreign_identity': c.identity.require_actor.return_value = replace(c.actor, tenant_id=uuid4())
    with pytest.raises(ApiIdentityAccessError): await initiate(c)
    assert counts(c) == (1, 0, 0, 0)


@pytest.mark.asyncio
async def test_processing_reservation_is_409_and_does_not_issue_a_second_token(workflow):
    c = workflow
    await initiate(c)
    record = c.env.session.execute(select(ApiIdempotencyRecord)).scalar_one()
    record.status, record.response_status, record.response_ciphertext = 'processing', None, None
    c.env.session.commit()
    before = counts(c)
    with pytest.raises(InProgress) as caught: await initiate(c)
    assert caught.value.status_code == 409
    assert counts(c) == before and c.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['initiate', 'consume'])
async def test_foreign_snapshot_binding_is_rejected_without_side_effects(workflow, method):
    c = workflow
    if method == 'initiate': await initiate(c)
    else: await consume(c, c.env.material.token)
    record = c.env.session.execute(select(ApiIdempotencyRecord).where(ApiIdempotencyRecord.operation == (INITIATE if method == 'initiate' else CONSUME))).scalar_one()
    snapshot = c.workflow.idempotency.response_codec.decrypt(record.response_ciphertext)
    snapshot['binding']['tenant_id'] = str(uuid4())
    record.response_ciphertext = c.workflow.idempotency.response_codec.encrypt(snapshot)
    c.env.session.commit()
    before = counts(c)
    with pytest.raises(OperationError):
        if method == 'initiate': await initiate(c)
        else: await consume(c, c.env.material.token)
    assert counts(c) == before


@pytest.mark.asyncio
async def test_consume_replay_still_requires_fresh_signed_telegram_proof(workflow):
    c = workflow
    await consume(c, c.env.material.token)
    before = counts(c)
    c.now += timedelta(minutes=20)
    with pytest.raises(TelegramAuthenticationError):
        await c.workflow.consume_binding(token=c.env.material.token, telegram_init_data=proof(), idempotency_key='consume-key')
    assert counts(c) == before


@pytest.mark.asyncio
async def test_expired_token_with_new_key_cannot_create_account_or_leave_processing_record(workflow):
    c = workflow
    c.now = NOW+timedelta(minutes=16)
    with pytest.raises(Denied): await consume(c, c.env.material.token)
    assert counts(c) == (1, 0, 0, 0) and c.commits == 0


@pytest.mark.asyncio
async def test_blocked_owner_cannot_replay_consume_success(workflow):
    c = workflow
    await consume(c, c.env.material.token)
    c.env.session.execute(update(c.env.users).where(c.env.users.c.id == c.env.owner).values(status='blocked'))
    c.env.session.commit()
    before = counts(c)
    with pytest.raises(Denied): await consume(c, c.env.material.token)
    assert counts(c) == before and c.commits == 1


@pytest.mark.asyncio
async def test_initiation_replay_after_consumption_preserves_original_secret_and_response(workflow):
    c = workflow
    first = await initiate(c)
    await consume(c, first.token)
    before = counts(c)
    repeated = await initiate(c)
    assert first == repeated and first.token == repeated.token
    assert repeated.row_version == 1  # The original initiation receipt, not the current token state.
    assert c.env.session.get(Token, first.binding_id).row_version == 2
    assert counts(c) == before


@pytest.mark.asyncio
async def test_missing_derivation_key_history_cannot_silently_replace_token_on_replay(workflow):
    c = workflow
    first = await initiate(c)
    before = counts(c)
    c.workflow.deriver = PlatformTelegramBindingTokenDeriver(encryption_key=Fernet.generate_key().decode('ascii'))
    with pytest.raises(OperationError): await initiate(c)
    assert counts(c) == before
    assert c.env.session.get(Token, first.binding_id).row_version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['initiate', 'consume'])
async def test_commit_failure_rolls_back_all_pending_workflow_effects(workflow, method):
    c = workflow
    async def failed_commit():
        assert c.env.session.execute(select(EventLog.id)).first() is not None
        raise RuntimeError('Simulated database commit failure.')
    c.env.bridge.commit = failed_commit
    with pytest.raises(RuntimeError):
        if method == 'initiate': await initiate(c)
        else: await consume(c, c.env.material.token)
    assert counts(c) == (1, 0, 0, 0)


def test_token_derivation_is_stable_domain_bound_and_uses_all_immutable_context():
    context = dict(binding_id=uuid4(), user_id=uuid4(), tenant_id=uuid4(), initiated_by_user_id=uuid4(), issued_at=NOW)
    deriver = PlatformTelegramBindingTokenDeriver(encryption_key=KEY)
    material = deriver.issue(**context)
    assert material.token == PlatformTelegramBindingTokenDeriver(encryption_key=KEY).issue(**context).token
    assert len(material.token) == 43 and material.token not in repr(material)
    for field in context:
        changed = dict(context)
        changed[field] = NOW+timedelta(microseconds=1) if field == 'issued_at' else uuid4()
        assert deriver.issue(**changed).token != material.token
    assert PlatformTelegramBindingTokenDeriver(encryption_key=Fernet.generate_key().decode('ascii')).issue(**context).token != material.token
    offset = timezone(timedelta(hours=3))
    assert deriver.issue(**{**context, 'issued_at': NOW.astimezone(offset)}).token == material.token


@pytest.mark.parametrize('key', [None, '', 'private-invalid-key', 'x'*44])
def test_token_derivation_rejects_missing_or_invalid_config_without_echo(key):
    with pytest.raises(ValueError) as error: PlatformTelegramBindingTokenDeriver(encryption_key=key)
    assert 'private-' not in str(error.value)
