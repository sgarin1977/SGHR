from uuid import uuid4

import pytest

from services.api_identity import (
    ApiActorContext,
)


def make_admin_actor(
    role: str,
) -> ApiActorContext:
    return ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role=role,
        roles=(role,),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    (
        "moderator",
        "admin",
        "finance_admin",
        "super_admin",
    ),
)
async def test_admin_guard_accepts_tz_admin_roles(
    role,
):
    from api.auth import require_admin_actor

    actor = make_admin_actor(role)

    result = await require_admin_actor(actor)

    assert result is actor


@pytest.mark.asyncio
async def test_admin_guard_is_fail_closed():
    from api.auth import require_admin_actor
    from api.errors import ApiHttpError

    actor = make_admin_actor("client")

    with pytest.raises(ApiHttpError) as error:
        await require_admin_actor(actor)

    assert error.value.status_code == 403
    assert error.value.code == (
        "admin_role_required"
    )


@pytest.mark.asyncio
async def test_admin_permission_guard_requires_role_and_permission():
    from api.auth import (
        require_admin_permission,
    )
    from api.errors import ApiHttpError

    permission = "admin.users.read"
    guard = require_admin_permission(
        permission
    )

    allowed = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=(permission,),
    )

    assert await guard(allowed) is allowed

    non_admin_with_permission = (
        ApiActorContext(
            user_id=uuid4(),
            tenant_id=uuid4(),
            active_role="client",
            roles=("client",),
            language_code="uk",
            timezone=None,
            status="active",
            permissions=(permission,),
        )
    )

    with pytest.raises(ApiHttpError) as role_error:
        await guard(non_admin_with_permission)

    assert role_error.value.status_code == 403
    assert role_error.value.code == (
        "admin_role_required"
    )

    admin_without_permission = (
        ApiActorContext(
            user_id=uuid4(),
            tenant_id=uuid4(),
            active_role="admin",
            roles=("admin",),
            language_code="uk",
            timezone=None,
            status="active",
            permissions=(),
        )
    )

    with pytest.raises(
        ApiHttpError
    ) as permission_error:
        await guard(admin_without_permission)

    assert (
        permission_error.value.status_code
        == 403
    )
    assert permission_error.value.code == (
        "permission_required"
    )


def test_admin_scope_context_uses_current_actor_and_fails_closed():
    from services.api_admin_access import (
        build_api_admin_scope_context,
    )
    from services.api_identity import (
        ApiRoleScopeContext,
    )

    tenant_id = uuid4()
    country_id = uuid4()
    other_country_id = uuid4()

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone=None,
        status="active",
        role_scopes=(
            ApiRoleScopeContext(
                role="admin",
                scope_type="country",
                scope_id=country_id,
                scope_code=None,
            ),
            ApiRoleScopeContext(
                role="admin",
                scope_type="language",
                scope_id=None,
                scope_code="UK",
            ),
        ),
        permissions=(
            "admin.users.read",
        ),
    )

    context = build_api_admin_scope_context(
        actor
    )

    assert context.tenant_id == tenant_id
    assert context.admin_user_id == actor.user_id
    assert context.is_global is False
    assert context.country_ids == frozenset(
        {country_id}
    )
    assert context.language_codes == frozenset(
        {"uk"}
    )

    assert context.allows(
        country_id=country_id,
        language_code="uk",
    )
    assert not context.allows(
        country_id=other_country_id,
        language_code="uk",
    )
    assert not context.allows(
        country_id=country_id,
        language_code="en",
    )
    assert not context.allows(
        country_id=None,
        language_code=None,
    )

    actor_without_scopes = (
        ApiActorContext(
            user_id=uuid4(),
            tenant_id=tenant_id,
            active_role="admin",
            roles=("admin",),
            language_code="uk",
            timezone=None,
            status="active",
            role_scopes=(),
            permissions=(
                "admin.users.read",
            ),
        )
    )

    empty_context = (
        build_api_admin_scope_context(
            actor_without_scopes
        )
    )

    assert empty_context.is_global is False
    assert not empty_context.allows(
        country_id=country_id,
        language_code="uk",
    )

