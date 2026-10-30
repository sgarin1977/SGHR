from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import httpx
import pytest
from sqlalchemy.dialects import postgresql
import api.routes.construction_invitation_recipient as routes
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service
from database.repositories.construction.construction_invitation_preview import ConstructionInvitationPreviewRepository
from services.construction.construction_invitation_preview import ConstructionInvitationPreviewService
from services.construction.construction_invitations import ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, hash_construction_invitation_token
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.module_access import ModuleDisabledError
from test_construction_1a_invitation_acceptance_http import http_case

@pytest.fixture
def preview_case():
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    tenant, invitation_id, user = (uuid4(), uuid4(), uuid4())
    invitation = SimpleNamespace(id=invitation_id, tenant_id=tenant, user_id=None, email='recipient@example.com', role='ADMIN', status='pending', row_version=7, expires_at=now + timedelta(days=7), grant_expires_at=None, token_hash=hash_construction_invitation_token('private-preview-token'), token_used_at=None)
    state = SimpleNamespace(now=now, invitation=invitation, actor=SimpleNamespace(user_id=user, tenant_id=uuid4(), status='active'), statements=[], rows=[(invitation, 'tenant', tenant)])

    async def execute(statement):
        state.statements.append(statement)
        return SimpleNamespace(all=lambda: state.rows)
    session = SimpleNamespace(in_transaction=lambda: True, execute=execute)
    for name in ('commit', 'rollback', 'flush', 'add'):
        setattr(session, name, AsyncMock(side_effect=AssertionError('Preview must not write.')))
    state.session = session
    state.identity = SimpleNamespace(session=object(), has_verified_email=AsyncMock(return_value=True))
    state.service = ConstructionInvitationPreviewService(session=session, repository=ConstructionInvitationPreviewRepository(session), identity_reader=state.identity, now_provider=lambda: state.now)
    return state

async def read(case):
    return await case.service.preview(actor=case.actor, tenant_id=case.invitation.tenant_id, invitation_id=case.invitation.id, token='private-preview-token')

@pytest.mark.asyncio
async def test_preview_reads_scopes_and_version_in_one_select_without_writes(preview_case):
    case = preview_case
    result = await read(case)
    assert result['row_version'] == 7 and result['status'] == 'pending'
    assert result['scopes'] == [{'scope_type': 'tenant', 'scope_id': str(case.invitation.tenant_id)}]
    assert set(result) == {'id', 'role', 'status', 'scopes', 'expires_at', 'grant_expires_at', 'row_version'}
    assert 'private-preview-token' not in repr(result)
    assert case.invitation.token_hash not in repr(result)
    assert len(case.statements) == 1
    statement = case.statements[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert 'LEFT OUTER JOIN' in sql and 'FOR UPDATE' not in sql
    assert 'construction_invitation_scopes.tenant_id = construction_invitations.tenant_id' in sql
    assert set(statement.compile().params.values()) == {case.invitation.id, case.invitation.tenant_id}
    for name in ('commit', 'rollback', 'flush', 'add'):
        getattr(case.session, name).assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'email', 'bound_user', 'token', 'expired', 'accepted', 'declined', 'revoked'])
async def test_preview_rejects_unavailable_invitation_without_writes(preview_case, failure):
    case = preview_case
    error = ConstructionInvitationTokenDeniedError
    if failure == 'missing':
        case.rows = []
    elif failure == 'email':
        case.identity.has_verified_email.return_value = False
        error = ConstructionInvitationRecipientDeniedError
    elif failure == 'bound_user':
        case.invitation.user_id = uuid4()
        error = ConstructionInvitationRecipientDeniedError
    elif failure == 'token':
        case.invitation.token_hash = '0' * 64
    elif failure == 'expired':
        case.invitation.expires_at = case.now
    else:
        case.invitation.status = failure
    with pytest.raises(error):
        await read(case)
    for name in ('commit', 'rollback', 'flush', 'add'):
        getattr(case.session, name).assert_not_called()

@pytest.fixture
def recipient_http(http_case):
    case = http_case
    result = {'id': str(case.material.invitation_id), 'role': 'ADMIN', 'status': 'pending', 'scopes': [{'scope_type': 'tenant', 'scope_id': str(case.material.tenant_id)}], 'expires_at': case.material.expires_at.isoformat(), 'grant_expires_at': None, 'row_version': 7}
    case.preview = SimpleNamespace(preview=AsyncMock(return_value=result))
    case.decline = SimpleNamespace(decline_invitation=AsyncMock(return_value={'id': str(case.material.invitation_id), 'status': 'declined', 'row_version': 5}))
    case.app.dependency_overrides[routes.get_preview_service] = lambda: case.preview
    case.app.dependency_overrides[routes.get_decline_workflow] = lambda: case.decline
    return case

async def request(case, operation, *, anonymous=False, changes=None):
    if not anonymous:
        case.app.dependency_overrides[get_current_actor] = lambda: case.actor
    body = dict(case.body)
    if operation == 'preview':
        body.pop('expected_row_version')
    body.update(changes or {})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app, raise_app_exceptions=False), base_url='http://testserver') as client:
        return await client.post('/api/v1/construction/invitations/' + operation, json=body, headers={'X-Request-ID': 'recipient-http'})

@pytest.mark.asyncio
async def test_recipient_preview_returns_current_version_without_idempotency_key(recipient_http):
    case = recipient_http
    response = await request(case, 'preview')
    assert response.status_code == 200, response.text
    assert response.json()['data']['row_version'] == 7
    case.preview.preview.assert_awaited_once_with(actor=case.actor, tenant_id=case.material.tenant_id, invitation_id=case.material.invitation_id, token=case.material.token)
    assert case.material.token not in response.text
    assert case.envelope.invitation_context not in response.text

@pytest.mark.asyncio
async def test_recipient_decline_uses_existing_workflow_without_idempotency_key(recipient_http):
    case = recipient_http
    response = await request(case, 'decline')
    assert response.status_code == 200, response.text
    assert response.json()['data'] == {'id': str(case.material.invitation_id), 'status': 'declined', 'row_version': 5}
    case.decline.decline_invitation.assert_awaited_once_with(actor=case.actor, tenant_id=case.material.tenant_id, invitation_id=case.material.invitation_id, expected_row_version=4, token=case.material.token, trace_id='recipient-http')

@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['preview', 'decline'])
@pytest.mark.parametrize('failure', ['anonymous', 'token', 'context', 'tenant', 'module', 'email', 'private'])
async def test_recipient_http_guards_and_redacts_private_fields(recipient_http, operation, failure):
    case = recipient_http
    changes = {}
    expected = 403
    target = case.preview.preview if operation == 'preview' else case.decline.decline_invitation
    if failure == 'anonymous':
        expected = 401
    elif failure == 'token':
        changes['token'] = 'invalid'
    elif failure == 'context':
        changes['invitation_context'] = 'invalid'
    elif failure == 'tenant':
        changes['tenant_id'] = str(uuid4())
        expected = 422
    elif failure == 'module':
        case.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == 'email':
        target.side_effect = ConstructionInvitationRecipientDeniedError()
    elif failure == 'private':
        target.return_value = {**target.return_value, 'token': case.material.token}
        expected = 500
    response = await request(case, operation, anonymous=failure == 'anonymous', changes=changes)
    assert response.status_code == expected, response.text
    assert case.material.token not in response.text
    assert case.envelope.invitation_context not in response.text
    if failure not in ('email', 'private'):
        target.assert_not_awaited()

@pytest.mark.asyncio
async def test_recipient_decline_preserves_row_version_conflict(recipient_http):
    case = recipient_http
    case.decline.decline_invitation.side_effect = ConstructionRowVersionConflictError()
    response = await request(case, 'decline')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'ROW_VERSION_CONFLICT'
