from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from database.models import User, UserRoleMapping
from services.api_identity import ApiIdentityAccessError, ApiIdentityService
from test_platform_role_tenant_isolation import role_store


@pytest.fixture
def identity_case():
    c = SimpleNamespace(user_id=uuid4(), tenant=uuid4(), other=uuid4())
    c.user = SimpleNamespace(
        id=c.user_id, tenant_id=None, status="active", active_role="client",
        language_code="uk", timezone="Europe/Kyiv",
    )
    c.user_service = SimpleNamespace(get_user_by_id=AsyncMock(return_value=c.user))
    c.repo = SimpleNamespace(
        has_active_tenant_membership=AsyncMock(return_value=True),
        list_active_roles=AsyncMock(return_value=["client"]),
        list_active_permissions=AsyncMock(return_value=["test.read"]),
        list_active_role_scopes=AsyncMock(return_value=[]),
    )
    c.session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    c.service = ApiIdentityService(
        session=c.session, user_service=c.user_service, user_repository=c.repo,
    )
    return c


async def actor(c, **changes):
    before = dict(vars(c.user))
    arguments = dict(user_id=c.user_id, tenant_id=c.tenant)
    arguments.update(changes)
    try:
        return await c.service.require_actor(**arguments)
    finally:
        assert dict(vars(c.user)) == before
        c.session.commit.assert_not_called()
        c.session.rollback.assert_not_called()


def test_global_user_model_already_allows_no_primary_tenant():
    column = User.__table__.c.tenant_id
    assert column.nullable is True
    assert {fk.target_fullname for fk in column.foreign_keys} == {"tenants.id"}
    assert User(tenant_id=None).tenant_id is None


@pytest.mark.asyncio
async def test_no_primary_tenant_uses_explicit_selected_tenant_with_secondary_membership(identity_case):
    c = identity_case
    result = await actor(c)
    assert result.user_id == c.user_id and result.tenant_id == c.tenant
    assert result.roles == ("client",) and result.permissions == ("test.read",)
    c.repo.has_active_tenant_membership.assert_awaited_once_with(user_id=c.user_id, tenant_id=c.tenant)
    c.repo.list_active_roles.assert_awaited_once_with(c.user_id, tenant_id=c.tenant)
    c.repo.list_active_role_scopes.assert_awaited_once_with(
        user_id=c.user_id, tenant_id=c.tenant, roles=("client",),
    )
    assert c.user.tenant_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["no_membership", "missing_checker", "blocked_user"])
async def test_no_primary_tenant_does_not_grant_access_automatically(identity_case, failure):
    c = identity_case
    if failure == "no_membership": c.repo.has_active_tenant_membership.return_value = False
    elif failure == "missing_checker": del c.repo.has_active_tenant_membership
    else: c.user.status = "blocked"
    with pytest.raises(ApiIdentityAccessError):
        await actor(c)
    c.repo.list_active_roles.assert_not_called()
    c.repo.list_active_permissions.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["user_id", "tenant_id"])
@pytest.mark.parametrize("invalid", [None, "", "not-a-uuid", True, 42])
async def test_invalid_request_identity_is_denied_before_user_lookup(identity_case, field, invalid):
    c = identity_case
    with pytest.raises(ApiIdentityAccessError):
        await actor(c, **{field: invalid})
    c.user_service.get_user_by_id.assert_not_called()
    c.repo.has_active_tenant_membership.assert_not_called()
    c.repo.list_active_roles.assert_not_called()


@pytest.mark.asyncio
async def test_null_primary_and_null_requested_tenant_never_count_as_membership(identity_case):
    c = identity_case
    c.repo.has_active_tenant_membership.return_value = False
    with pytest.raises(ApiIdentityAccessError):
        await actor(c, tenant_id=None)
    c.user_service.get_user_by_id.assert_not_called()
    c.repo.list_active_roles.assert_not_called()


@pytest.mark.asyncio
async def test_existing_primary_tenant_behavior_is_preserved(identity_case):
    c = identity_case
    c.user.tenant_id = c.tenant
    result = await actor(c)
    assert result.tenant_id == c.tenant
    c.repo.has_active_tenant_membership.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_status", ["active", "revoked", None])
async def test_real_secondary_roles_remain_tenant_scoped_with_null_primary(identity_case, role_store, target_status):
    c = identity_case
    sql_session, _table, repository = role_store
    sql_session.add(UserRoleMapping(
        id=uuid4(), user_id=c.user_id, tenant_id=c.other, role="admin", status="active",
    ))
    if target_status is not None:
        sql_session.add(UserRoleMapping(
            id=uuid4(), user_id=c.user_id, tenant_id=c.tenant, role="client", status=target_status,
        ))
    sql_session.commit()
    repository.list_active_permissions = AsyncMock(return_value=["test.read"])
    repository.list_active_role_scopes = AsyncMock(return_value=[])
    c.user.active_role = "admin"
    c.service = ApiIdentityService(
        session=c.session, user_service=c.user_service, user_repository=repository,
    )
    if target_status == "active":
        result = await actor(c)
        assert result.tenant_id == c.tenant and result.roles == ("client",)
        assert result.active_role is None
        repository.list_active_permissions.assert_awaited_once_with(roles=("client",))
    else:
        with pytest.raises(ApiIdentityAccessError):
            await actor(c)
        repository.list_active_permissions.assert_not_called()
    assert c.user.tenant_id is None and c.user.active_role == "admin"
