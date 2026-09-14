from uuid import uuid4

import pytest

from services.api_identity import (
    ApiActorContext,
)


def make_actor(
    role: str,
    permissions: tuple[str, ...] = (),
) -> ApiActorContext:
    return ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role=role,
        roles=(role,),
        language_code="uk",
        timezone=None,
        status="active",
        permissions=permissions,
    )


@pytest.mark.asyncio
async def test_super_admin_guard_accepts_only_super_admin():
    from api.auth import (
        require_super_admin_actor,
    )
    from api.errors import ApiHttpError

    super_admin = make_actor(
        "super_admin"
    )

    assert (
        await require_super_admin_actor(
            super_admin
        )
        is super_admin
    )

    for role in (
        "admin",
        "moderator",
        "finance_admin",
        "client",
        "root",
    ):
        actor = make_actor(role)

        with pytest.raises(
            ApiHttpError
        ) as error:
            await require_super_admin_actor(
                actor
            )

        assert error.value.status_code == 403
        assert error.value.code == (
            "super_admin_role_required"
        )


@pytest.mark.asyncio
async def test_super_admin_permission_guard_requires_role_and_permission():
    from api.auth import (
        require_super_admin_permission,
    )
    from api.errors import ApiHttpError

    permission = (
        "super_admin.countries.read"
    )
    guard = require_super_admin_permission(
        permission
    )

    allowed = make_actor(
        "super_admin",
        permissions=(permission,),
    )

    assert await guard(allowed) is allowed

    super_admin_without_permission = (
        make_actor("super_admin")
    )

    with pytest.raises(
        ApiHttpError
    ) as permission_error:
        await guard(
            super_admin_without_permission
        )

    assert (
        permission_error.value.status_code
        == 403
    )
    assert permission_error.value.code == (
        "permission_required"
    )

    admin_with_permission = make_actor(
        "admin",
        permissions=(permission,),
    )

    with pytest.raises(
        ApiHttpError
    ) as role_error:
        await guard(admin_with_permission)

    assert role_error.value.status_code == 403
    assert role_error.value.code == (
        "super_admin_role_required"
    )

