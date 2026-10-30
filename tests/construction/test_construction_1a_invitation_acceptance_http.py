from construction_rate_limit_fixture import with_local_construction_rate_limit
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import httpx
import pytest
from cryptography.fernet import Fernet
from api.app import create_app
from api.construction_recipient_rate_limit import get_invitation_recipient_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
import api.routes.construction_invitations as routes
from database.repositories.user import PlatformInvitationMembershipConflictError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_invitation_delivery_material import ConstructionInvitationDeliveryMaterial
from services.construction.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec
from services.construction.construction_invitation_workflows import ConstructionInvitationIdempotencyInProgressError
from services.construction.construction_invitations import ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError
from services.module_access import SuiteDisabledError, ModuleDisabledError

@pytest.fixture
def http_case():
    key = Fernet.generate_key().decode('ascii')
    actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4(), status='active', permissions=())
    material = ConstructionInvitationDeliveryMaterial(invitation_id=uuid4(), tenant_id=uuid4(), email='recipient@example.com', expires_at=datetime.now(timezone.utc) + timedelta(days=7), token='private-http-token')
    codec = ConstructionInvitationRecipientContextCodec(encryption_key=key)
    envelope = codec.issue_for_delivery(material=material)
    response = {'id': str(material.invitation_id), 'status': 'accepted', 'row_version': 5}
    workflow = SimpleNamespace(accept_invitation=AsyncMock(return_value=response))
    suite = SimpleNamespace(require_enabled=AsyncMock())
    module = SimpleNamespace(require_enabled=AsyncMock())
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[routes.get_recipient_context_codec] = lambda: codec
    app.dependency_overrides[routes.get_acceptance_workflow] = lambda: workflow
    app.dependency_overrides[get_tenant_suite_access_service] = lambda: suite
    app.dependency_overrides[get_tenant_module_access_service] = lambda: module
    return SimpleNamespace(app=app, actor=actor, material=material, codec=codec, envelope=envelope, workflow=workflow, suite=suite, module=module, body={'invitation_context': envelope.invitation_context, 'token': envelope.token, 'expected_row_version': 4})

async def post(case, *, authenticated=True, body=None, headers=None, query=''):
    if authenticated:
        case.app.dependency_overrides[get_invitation_recipient_actor] = lambda: case.actor
    else:
        case.app.dependency_overrides.pop(get_invitation_recipient_actor, None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app, raise_app_exceptions=False), base_url='http://testserver') as client:
        return await client.post('/api/v1/construction/invitations/accept' + query, json=case.body if body is None else body, headers={'Idempotency-Key': 'accept-key', 'X-Request-ID': 'accept-http'} if headers is None else headers)

@pytest.mark.asyncio
async def test_acceptance_http_requires_authentication(http_case):
    case = http_case
    response = await post(case, authenticated=False)
    assert response.status_code == 401, response.text
    case.workflow.accept_invitation.assert_not_awaited()
    case.suite.require_enabled.assert_not_awaited()

@pytest.mark.asyncio
async def test_acceptance_http_uses_invitation_tenant_without_recipient_membership(http_case):
    case = http_case
    response = await post(case, query='?tenant_id=' + str(uuid4()))
    assert response.status_code == 200, response.text
    assert response.json() == {'data': {'id': str(case.material.invitation_id), 'status': 'accepted', 'row_version': 5}, 'meta': {'organization_id': str(case.material.tenant_id)}, 'request_id': 'accept-http'}
    assert response.headers['X-Request-ID'] == 'accept-http'
    case.suite.require_enabled.assert_awaited_once_with(tenant_id=case.material.tenant_id, suite_code='construction')
    case.module.require_enabled.assert_awaited_once_with(tenant_id=case.material.tenant_id, module_code='construction.core')
    case.workflow.accept_invitation.assert_awaited_once_with(actor=case.actor, tenant_id=case.material.tenant_id, invitation_id=case.material.invitation_id, expected_row_version=4, token=case.envelope.token, idempotency_key='accept-key', trace_id='accept-http')
    assert case.actor.tenant_id != case.material.tenant_id
    assert case.envelope.token not in response.text
    assert case.envelope.invitation_context not in response.text

@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [{}, {'Idempotency-Key': ''}, {'Idempotency-Key': '   '}, {'Idempotency-Key': 'x' * 256}])
async def test_acceptance_http_requires_valid_idempotency_header(http_case, headers):
    case = http_case
    response = await post(case, headers=headers)
    assert response.status_code == 422, response.text
    case.workflow.accept_invitation.assert_not_awaited()
    assert case.envelope.token not in response.text

@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('expected_row_version', True), ('expected_row_version', '4'), ('expected_row_version', 0), ('expected_row_version', -1), ('token', ''), ('token', 7), ('invitation_context', ''), ('tenant_id', 'untrusted'), ('invitation_id', 'untrusted'), ('role', 'OWNER'), ('scopes', [])])
async def test_acceptance_http_rejects_invalid_body_without_workflow(http_case, field, value):
    case = http_case
    body = {**case.body, field: value}
    response = await post(case, body=body)
    assert response.status_code == 422, response.text
    case.workflow.accept_invitation.assert_not_awaited()
    assert case.envelope.token not in response.text
    assert case.envelope.invitation_context not in response.text

@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['token', 'invitation_context'])
async def test_acceptance_http_rejects_untrusted_context_before_module_checks(http_case, field):
    case = http_case
    response = await post(case, body={**case.body, field: 'invalid'})
    assert response.status_code == 403, response.text
    case.suite.require_enabled.assert_not_awaited()
    case.module.require_enabled.assert_not_awaited()
    case.workflow.accept_invitation.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize('disabled', ['suite', 'module'])
async def test_acceptance_http_returns_module_disabled_for_invitation_tenant(http_case, disabled):
    case = http_case
    if disabled == 'suite':
        case.suite.require_enabled.side_effect = SuiteDisabledError()
    else:
        case.module.require_enabled.side_effect = ModuleDisabledError()
    response = await post(case)
    assert response.status_code == 403, response.text
    assert response.json()['error']['code'] == 'MODULE_DISABLED'
    case.workflow.accept_invitation.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize('error,status,code', [(ConstructionRowVersionConflictError(), 409, 'ROW_VERSION_CONFLICT'), (PlatformInvitationMembershipConflictError(), 409, 'PLATFORM_MEMBERSHIP_CONFLICT'), (ApiIdempotencyKeyReusedError(), 409, 'IDEMPOTENCY_KEY_REUSED'), (ConstructionInvitationIdempotencyInProgressError(), 409, 'idempotency_in_progress'), (ConstructionInvitationRecipientDeniedError(), 403, 'forbidden'), (ConstructionInvitationTokenDeniedError(), 403, 'forbidden'), (RuntimeError('private-http-token'), 500, 'internal_error')])
async def test_acceptance_http_maps_errors_without_secrets(http_case, error, status, code):
    case = http_case
    case.workflow.accept_invitation.side_effect = error
    response = await post(case)
    assert response.status_code == status, response.text
    assert response.json()['error']['code'] == code
    assert response.json()['error']['request_id'] == 'accept-http'
    assert case.envelope.token not in response.text
    assert case.envelope.invitation_context not in response.text

@pytest.mark.asyncio
async def test_acceptance_http_rejects_private_fields_in_workflow_response(http_case):
    case = http_case
    case.workflow.accept_invitation.return_value = {'id': str(case.material.invitation_id), 'status': 'accepted', 'row_version': 5, 'token': case.envelope.token}
    response = await post(case)
    assert response.status_code == 500
    assert case.envelope.token not in response.text

@pytest.mark.asyncio
async def test_acceptance_session_binds_verified_invitation_context(monkeypatch, http_case):
    case = http_case
    context = case.codec.resolve(invitation_context=case.envelope.invitation_context, token=case.envelope.token)
    session = object()
    calls = []

    @asynccontextmanager
    async def transaction(*, tenant_id):
        calls.append(tenant_id)
        yield session
    monkeypatch.setattr(routes, 'construction_transaction', transaction)
    received = [item async for item in routes.get_acceptance_scoped_session(context=context)]
    assert received == [session] and calls == [case.material.tenant_id]

def test_acceptance_workflow_factory_keeps_platform_identity_separate(monkeypatch, http_case):
    case = http_case
    construction, platform = (object(), object())
    calls = []
    expected = object()

    def build(**kwargs):
        calls.append(kwargs)
        return expected
    monkeypatch.setattr(routes, 'build_construction_invitation_acceptance_workflow', build)
    result = routes.get_acceptance_workflow(construction_session=construction, platform_session=platform, settings=SimpleNamespace(encryption_key='configured-key'))
    assert result is expected
    assert calls[0]['session'] is construction
    assert calls[0]['identity_reader'].session is platform
    assert calls[0]['idempotency_encryption_key'] == 'configured-key'

def test_acceptance_openapi_requires_idempotency_key():
    operation = with_local_construction_rate_limit(create_app()).openapi()['paths']['/api/v1/construction/invitations/accept']['post']
    header = next(item for item in operation['parameters'] if item['name'] == 'Idempotency-Key')
    assert header['in'] == 'header' and header['required'] is True

@pytest.mark.asyncio
async def test_acceptance_http_rejects_inactive_actor_before_module_checks(http_case):
    case = http_case
    case.actor.status = 'blocked'
    response = await post(case)
    assert response.status_code == 403
    case.suite.require_enabled.assert_not_awaited()
    case.workflow.accept_invitation.assert_not_awaited()

def test_acceptance_factory_rejects_shared_identity_and_write_session():
    from api.errors import ApiHttpError
    session = object()
    with pytest.raises(ApiHttpError) as caught:
        routes.get_acceptance_workflow(construction_session=session, platform_session=session, settings=SimpleNamespace(encryption_key='configured-key'))
    assert caught.value.status_code == 500

# invitation_identity_only_v1

# invitation_binding_session_v1
