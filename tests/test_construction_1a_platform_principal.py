import asyncio
from contextlib import nullcontext
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from api.app import create_app
from api.auth import get_access_token_codec, get_current_actor
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal, get_platform_principal_service
from api.security import AccessTokenCodec
from database.repositories.api_platform_principal import ApiPlatformPrincipalRepository
from services.api_identity import ApiActorContext, ApiIdentityAccessError, ApiIdentityService
from services.api_platform_principal import (
    ApiPlatformPrincipal, ApiPlatformPrincipalOperationError, ApiPlatformPrincipalService, build_api_platform_principal_service,
)


@pytest.fixture
def canonical():
    c = SimpleNamespace(user=uuid4(), other=uuid4(), old_tenant=uuid4(), statements=[], engine=create_engine('sqlite://'))
    with c.engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users(id CHAR(32) PRIMARY KEY,status TEXT NOT NULL,tenant_id CHAR(32))')
        connection.exec_driver_sql('INSERT INTO users VALUES(?,?,NULL),(?,?,?)',
            (c.user.hex, 'active', c.other.hex, 'blocked', c.old_tenant.hex))
    c.sync = Session(c.engine)
    class Adapter:
        @property
        def no_autoflush(self): return c.sync.no_autoflush
        async def execute(self, statement):
            c.statements.append(statement)
            return c.sync.execute(statement)
    c.session = Adapter()
    c.service = build_api_platform_principal_service(session=c.session)
    c.codec = AccessTokenCodec(secret='test-platform-principal-secret-at-least-32-characters',
        issuer='principal-tests', audience='platform', access_ttl_seconds=600)
    c.token = c.codec.issue_access_token(user_id=c.user, tenant_id=c.old_tenant)
    c.app = create_app()
    c.app.dependency_overrides[get_access_token_codec] = lambda: c.codec
    c.app.dependency_overrides[get_api_session] = lambda: c.session
    async def probe(principal=Depends(get_authenticated_platform_principal)):
        return {'user_id': str(principal.user_id)}
    # Test-only endpoint. The installer never changes api/app.py or registers it.
    c.app.add_api_route('/_principal_probe', probe, methods=['GET'])
    try: yield c
    finally: c.sync.close(); c.engine.dispose()


async def request(c, token=None, *, authorization=None, params=None):
    headers = {'Authorization': 'Bearer ' + (c.token if token is None else token),
        'X-Tenant-ID': str(uuid4()), 'X-User-ID': str(c.other), 'X-Request-ID': 'principal-tests'}
    if authorization is not None:
        if authorization == 'absent': headers.pop('Authorization')
        else: headers['Authorization'] = authorization
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False), base_url='http://testserver') as client:
        return await client.get('/_principal_probe', headers=headers, params=params)


@pytest.mark.asyncio
async def test_existing_valid_platform_jwt_resolves_active_orgless_user_without_tenant_authority(canonical):
    c = canonical
    response = await request(c, params={'user_id': str(c.other), 'tenant_id': str(uuid4())})
    assert response.status_code == 200 and response.json() == {'user_id': str(c.user)}
    principal = await c.service.require_principal(user_id=c.user)
    assert not isinstance(principal, ApiActorContext)
    assert set(principal.__dataclass_fields__) == {'user_id'}
    assert not any(hasattr(principal, name) for name in ('tenant_id', 'roles', 'permissions', 'active_role'))
    with pytest.raises(FrozenInstanceError): principal.user_id = c.other
    assert len(c.statements) == 2
    for statement in c.statements:
        assert [column.name for column in statement.selected_columns] == ['id', 'status']
        assert statement.get_execution_options()['autoflush'] is False
        assert not statement._for_update_arg and 'user_roles' not in str(statement)
    assert not c.sync.dirty and not c.sync.new and not c.sync.deleted


@pytest.mark.asyncio
async def test_tenant_gated_actor_still_denies_same_user_and_token(canonical):
    c = canonical
    users = SimpleNamespace(get_user_by_id=AsyncMock(return_value=SimpleNamespace(id=c.user, status='active', tenant_id=None)))
    repository = SimpleNamespace(has_active_tenant_membership=AsyncMock(return_value=False))
    identity = ApiIdentityService(c.session, user_service=users, user_repository=repository)
    with pytest.raises(ApiHttpError) as caught:
        await get_current_actor(token=c.token, codec=c.codec, identity_service=identity)
    assert caught.value.status_code == 401
    assert (await request(c)).status_code == 200
    repository.has_active_tenant_membership.assert_awaited_once_with(user_id=c.user, tenant_id=c.old_tenant)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['blocked', 'deleted', 'inactive', 'pending', 'missing'])
async def test_changed_canonical_identity_is_freshly_checked_after_success(canonical, status):
    c = canonical
    assert (await request(c)).status_code == 200
    if status == 'missing': c.sync.execute(text('DELETE FROM users WHERE id=:id'), {'id': c.user.hex})
    else: c.sync.execute(text('UPDATE users SET status=:status WHERE id=:id'), {'id': c.user.hex, 'status': status})
    c.sync.commit()
    response = await request(c)
    assert response.status_code == 401 and response.json()['error']['code'] == 'invalid_token'
    assert response.headers['WWW-Authenticate'] == 'Bearer error="invalid_token"'
    assert len(c.statements) == 2 and str(c.user) not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('authorization', ['absent', 'Basic abc', 'ApiKey abc', 'Bearer '])
async def test_existing_bearer_authentication_is_required_before_identity_read(canonical, authorization):
    c = canonical
    response = await request(c, authorization=authorization)
    assert response.status_code == 401 and c.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['malformed', 'expired', 'signature', 'issuer', 'audience', 'type', 'user', 'tenant', 'missing_claim', 'future', 'algorithm'])
async def test_existing_codec_rejects_invalid_tokens_before_user_query(canonical, bad):
    c = canonical
    payload = jwt.decode(c.token, c.codec.secret, algorithms=['HS256'], audience=c.codec.audience, issuer=c.codec.issuer)
    key, algorithm = c.codec.secret, 'HS256'
    if bad == 'malformed': token = 'private-invalid-token'
    else:
        if bad == 'expired': payload['exp'] = int((datetime.now(timezone.utc)-timedelta(seconds=5)).timestamp())
        elif bad == 'signature': key = 'a-different-test-secret-at-least-32-characters'
        elif bad == 'issuer': payload['iss'] = 'other'
        elif bad == 'audience': payload['aud'] = 'other'
        elif bad == 'type': payload['typ'] = 'refresh'
        elif bad == 'user': payload['sub'] = 'not-a-user-uuid'
        elif bad == 'tenant': payload['tenant_id'] = 'not-a-tenant-uuid'
        elif bad == 'missing_claim': del payload['jti']
        elif bad == 'future': payload['nbf'] = int((datetime.now(timezone.utc)+timedelta(minutes=5)).timestamp())
        elif bad == 'algorithm': algorithm, key = 'none', ''
        token = jwt.encode(payload, key, algorithm=algorithm)
    response = await request(c, token)
    assert response.status_code == 401 and c.statements == []
    assert 'private-invalid-token' not in response.text and 'data' not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [None, True, 'user', uuid4()])
async def test_invalid_or_missing_canonical_user_is_denied(canonical, bad):
    c = canonical
    with pytest.raises(ApiIdentityAccessError): await c.service.require_principal(user_id=bad)
    if not isinstance(bad, UUID): assert c.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['wrong_user', 'bool', 'string', 'none', 'storage', 'cancel'])
async def test_service_rejects_bad_projection_and_sanitizes_storage_failures(canonical, bad):
    c = canonical
    repository = SimpleNamespace(session=c.session, get_active_user_id=AsyncMock())
    if bad == 'storage': repository.get_active_user_id.side_effect = RuntimeError('private storage details')
    elif bad == 'cancel': repository.get_active_user_id.side_effect = asyncio.CancelledError()
    else: repository.get_active_user_id.return_value = {'wrong_user': c.other, 'bool': True, 'string': str(c.user), 'none': None}[bad]
    service = ApiPlatformPrincipalService(repository=repository)
    error = asyncio.CancelledError if bad == 'cancel' else ApiPlatformPrincipalOperationError if bad == 'storage' else ApiIdentityAccessError
    with pytest.raises(error) as caught: await service.require_principal(user_id=c.user)
    assert 'private storage details' not in str(caught.value)


@pytest.mark.asyncio
async def test_dependency_storage_failure_is_generic_and_does_not_fall_back_to_tenant_actor(canonical):
    c = canonical
    c.app.dependency_overrides[get_platform_principal_service] = lambda: SimpleNamespace(
        require_principal=AsyncMock(side_effect=ApiPlatformPrincipalOperationError('private storage')))
    response = await request(c)
    assert response.status_code == 503 and response.json()['error']['code'] == 'service_unavailable'
    assert 'private storage' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('row', [(uuid4(), 'active'), (uuid4(), 'blocked'), ('not-a-uuid', 'active'), (True, 'active')])
async def test_repository_validates_projected_identity(canonical, row):
    c = canonical
    session = SimpleNamespace(no_autoflush=nullcontext(), execute=AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: row)))
    with pytest.raises(ValueError): await ApiPlatformPrincipalRepository(session).get_active_user_id(user_id=c.user)


def test_factory_binds_only_existing_platform_session_and_has_no_route_registration(canonical):
    c = canonical
    service = get_platform_principal_service(c.session)
    assert service.session is service.repository.session is c.session
    assert get_platform_principal_service.__defaults__[0].dependency is get_api_session
    for bad in (None, '', object()):
        with pytest.raises(ApiIdentityAccessError): ApiPlatformPrincipal(bad)
    assert get_authenticated_platform_principal.__name__ == 'get_authenticated_platform_principal'
