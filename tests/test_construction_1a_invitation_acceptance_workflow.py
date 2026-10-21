import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
import pytest
from cryptography.fernet import Fernet
from services.api_idempotency import ApiIdempotencyService, ApiIdempotencyResponseCodec, ApiIdempotencyKeyReusedError
from services.construction_invitation_acceptance_workflow import ConstructionInvitationAcceptanceWorkflow
from services.construction_invitation_workflows import ConstructionInvitationIdempotencyInProgressError
from services.construction_invitations import ConstructionInvitationOperationError, ConstructionInvitationRecipientDeniedError, ConstructionInvitationValidationError, hash_construction_invitation_token
from test_construction_1a_invitation_acceptance_service import acceptance_case, stored

@pytest.fixture
def pipeline(acceptance_case):
    case = acceptance_case
    case.db.rollback()
    case.db.execute('CREATE TABLE acceptance_ledger (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    case.db.commit()
    flags = SimpleNamespace(fail_complete=False, fail_commit=False)

    def encode(value):
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    class Ledger:
        reserve_record = None

        def __init__(self):
            self.session = case.session

        def save(self, record):
            case.db.execute('INSERT OR REPLACE INTO acceptance_ledger VALUES (?, ?)', (str(record.id), json.dumps(vars(record), default=encode, sort_keys=True)))

        async def get_active_record_for_update(self, **fields):
            for payload, in case.db.execute('SELECT payload FROM acceptance_ledger'):
                data = json.loads(payload)
                for key in ('id', 'tenant_id', 'principal_id'):
                    data[key] = UUID(data[key])
                for key in ('created_at', 'completed_at', 'expires_at'):
                    if data[key] is not None:
                        data[key] = datetime.fromisoformat(data[key])
                if data['response_ciphertext'] is not None:
                    data['response_ciphertext'] = bytes.fromhex(data['response_ciphertext'])
                if all((data[key] == fields[key] for key in ('tenant_id', 'principal_type', 'principal_id', 'operation', 'key_hash'))) and data['expires_at'] > fields['now']:
                    return SimpleNamespace(**data)
            return None

        async def create_record(self, **fields):
            record = SimpleNamespace(id=uuid4(), **fields, status='processing', created_at=case.now, completed_at=None, response_status=None, response_ciphertext=None)
            self.save(record)
            return record

        async def complete_record(self, *, record, **fields):
            if flags.fail_complete:
                raise RuntimeError('simulated completion failure')
            for key, value in fields.items():
                setattr(record, key, value)
            record.status = 'completed'
            self.save(record)
            return record
    original_execute = case.session.execute

    async def execute(statement, parameters):
        if set(parameters) == {'tenant_id'}:
            assert 'set_config' in str(statement)
            assert parameters['tenant_id'] == str(case.invitation.tenant_id)
            return
        await original_execute(statement, parameters)

    async def commit():
        if flags.fail_commit:
            raise RuntimeError('simulated commit failure')
        case.db.commit()
    case.session.execute = execute
    case.session.commit = AsyncMock(side_effect=commit)
    case.service.accept_invitation = AsyncMock(wraps=case.service.accept_invitation)
    codec = ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode('ascii'))
    idempotency = ApiIdempotencyService(repository=Ledger(), response_codec=codec)
    workflow = ConstructionInvitationAcceptanceWorkflow(session=case.session, acceptance_service=case.service, idempotency_service=idempotency)
    return SimpleNamespace(case=case, flags=flags, codec=codec, idempotency=idempotency, workflow=workflow)

async def invoke(pipeline, **changes):
    case = pipeline.case
    if not case.db.in_transaction:
        case.db.execute('BEGIN')
    fields = dict(actor=case.actor, tenant_id=case.invitation.tenant_id, invitation_id=case.invitation.id, expected_row_version=4, token='one-time-secret', idempotency_key='accept-key', trace_id='accept-workflow')
    fields.update(changes)
    return await pipeline.workflow.accept_invitation(**fields)

def ledger(pipeline):
    return [json.loads(row[0]) for row in pipeline.case.db.execute('SELECT payload FROM acceptance_ledger ORDER BY id')]

def snapshot(pipeline):
    return ({name: stored(pipeline.case, name) for name in ('memberships', 'grants', 'audits', 'invitations')}, ledger(pipeline))

@pytest.mark.asyncio
async def test_acceptance_workflow_commits_all_writes_and_encrypted_result_once(pipeline):
    case = pipeline.case
    result = await invoke(pipeline)
    assert result == {'id': str(case.invitation.id), 'status': 'accepted', 'row_version': 5}
    assert len(stored(case, 'memberships')) == 1
    assert len(stored(case, 'grants')) == 2
    assert len(stored(case, 'audits')) == 3
    case.session.commit.assert_awaited_once()
    records = ledger(pipeline)
    assert len(records) == 1
    record = records[0]
    assert record['status'] == 'completed' and record['response_status'] == 200
    assert record['operation'] == 'construction.invitation.accept'
    assert record['tenant_id'] == str(case.invitation.tenant_id)
    assert record['principal_id'] == str(case.actor.user_id)
    assert pipeline.codec.decrypt(bytes.fromhex(record['response_ciphertext'])) == result
    assert 'one-time-secret' not in json.dumps(record)
    assert case.invitation.token_hash not in json.dumps(record)

@pytest.mark.asyncio
async def test_acceptance_replay_returns_saved_response_without_duplicate_access(pipeline):
    case = pipeline.case
    case.invitation.expires_at = case.now + timedelta(minutes=30)
    first = await invoke(pipeline)
    before = snapshot(pipeline)
    case.now += timedelta(hours=1)
    replay = await invoke(pipeline)
    assert replay == first and snapshot(pipeline) == before
    case.service.accept_invitation.assert_awaited_once()
    assert case.identity.has_verified_email.await_count == 2
    assert case.session.commit.await_count == 2

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['audit', 'complete', 'commit'])
async def test_acceptance_workflow_failure_rolls_back_business_and_reservation(pipeline, failure):
    case = pipeline.case
    before = snapshot(pipeline)
    if failure == 'audit':
        case.fail = 'audit:3'
    elif failure == 'complete':
        pipeline.flags.fail_complete = True
    else:
        pipeline.flags.fail_commit = True
    with pytest.raises(ConstructionInvitationOperationError):
        await invoke(pipeline)
    assert snapshot(pipeline) == before
    assert case.session.rollback.await_count >= 1

@pytest.mark.asyncio
@pytest.mark.parametrize('key', [None, '', '   ', 7])
async def test_acceptance_requires_idempotency_key_before_business_writes(pipeline, key):
    before = snapshot(pipeline)
    with pytest.raises(ConstructionInvitationValidationError):
        await invoke(pipeline, idempotency_key=key)
    assert snapshot(pipeline) == before
    pipeline.case.service.accept_invitation.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['token', 'invitation_id', 'expected_row_version'])
async def test_acceptance_same_key_rejects_changed_payload(pipeline, changed):
    await invoke(pipeline)
    before = snapshot(pipeline)
    value = {'token': 'different', 'invitation_id': uuid4(), 'expected_row_version': 5}[changed]
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await invoke(pipeline, **{changed: value})
    assert snapshot(pipeline) == before
    pipeline.case.service.accept_invitation.assert_awaited_once()

@pytest.mark.asyncio
async def test_acceptance_processing_reservation_does_not_create_access(pipeline):
    case = pipeline.case
    case.db.execute('BEGIN')
    reservation = await pipeline.idempotency.reserve(tenant_id=case.invitation.tenant_id, principal_type='user', principal_id=case.actor.user_id, operation='construction.invitation.accept', idempotency_key='accept-key', payload={'invitation_id': str(case.invitation.id), 'expected_row_version': 4, 'token_hash': hash_construction_invitation_token('one-time-secret')}, now=case.now)
    assert not reservation.is_replay
    case.db.commit()
    before = snapshot(pipeline)
    with pytest.raises(ConstructionInvitationIdempotencyInProgressError):
        await invoke(pipeline)
    assert snapshot(pipeline) == before
    case.service.accept_invitation.assert_not_awaited()

@pytest.mark.asyncio
async def test_acceptance_replay_rechecks_current_verified_email(pipeline):
    await invoke(pipeline)
    before = snapshot(pipeline)
    pipeline.case.identity.has_verified_email.return_value = False
    with pytest.raises(ConstructionInvitationRecipientDeniedError):
        await invoke(pipeline)
    assert snapshot(pipeline) == before
    pipeline.case.service.accept_invitation.assert_awaited_once()

@pytest.mark.asyncio
async def test_acceptance_replay_rejects_secret_fields_in_saved_response(pipeline):
    case = pipeline.case
    await invoke(pipeline)
    record = await pipeline.idempotency.repository.get_active_record_for_update(tenant_id=case.invitation.tenant_id, principal_type='user', principal_id=case.actor.user_id, operation='construction.invitation.accept', key_hash=ledger(pipeline)[0]['key_hash'], now=case.now)
    record.response_ciphertext = pipeline.codec.encrypt({'id': str(case.invitation.id), 'status': 'accepted', 'row_version': 5, 'token': 'secret'})
    pipeline.idempotency.repository.save(record)
    case.db.commit()
    before = snapshot(pipeline)
    with pytest.raises(ConstructionInvitationOperationError):
        await invoke(pipeline)
    assert snapshot(pipeline) == before

def test_acceptance_workflow_rejects_split_idempotency_session(pipeline):
    pipeline.idempotency.repository.session = object()
    with pytest.raises(ValueError, match='shared'):
        ConstructionInvitationAcceptanceWorkflow(session=pipeline.case.session, acceptance_service=pipeline.case.service, idempotency_service=pipeline.idempotency)

def test_acceptance_workflow_factory_uses_existing_platform_idempotency(pipeline):
    from database.repositories.api_idempotency import ApiIdempotencyRepository
    from services.construction_invitation_acceptance_workflow import build_construction_invitation_acceptance_workflow
    case = pipeline.case
    workflow = build_construction_invitation_acceptance_workflow(session=case.session, identity_reader=case.identity, idempotency_encryption_key=Fernet.generate_key().decode('ascii'), now_provider=lambda: case.now)
    assert workflow.session is case.session
    assert workflow.acceptance_service.session is case.session
    assert workflow.acceptance_service.context.identity_reader is case.identity
    assert isinstance(workflow.idempotency_service.repository, ApiIdempotencyRepository)
    assert workflow.idempotency_service.repository.session is case.session
