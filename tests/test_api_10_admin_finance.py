import pytest


@pytest.mark.asyncio
async def test_finance_guard_requires_role_and_finance_permission():
    from uuid import uuid4

    from api.auth import (
        require_finance_permission,
    )
    from api.errors import ApiHttpError
    from services.api_identity import (
        ApiActorContext,
    )

    permission = "finance.read"
    guard = require_finance_permission(
        permission
    )

    finance_actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="finance_admin",
        roles=("finance_admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(permission,),
    )

    assert (
        await guard(finance_actor)
        is finance_actor
    )

    ordinary_admin = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(permission,),
    )

    with pytest.raises(ApiHttpError) as error:
        await guard(ordinary_admin)

    assert error.value.status_code == 403
    assert error.value.code == (
        "finance_admin_role_required"
    )

    finance_without_permission = (
        ApiActorContext(
            user_id=uuid4(),
            tenant_id=uuid4(),
            active_role="finance_admin",
            roles=("finance_admin",),
            language_code="uk",
            timezone="Europe/Kyiv",
            status="active",
            permissions=(),
        )
    )

    with pytest.raises(ApiHttpError) as error:
        await guard(
            finance_without_permission
        )

    assert error.value.status_code == 403
    assert error.value.code == (
        "permission_required"
    )

    with pytest.raises(ValueError):
        require_finance_permission(
            "admin.logs.read"
        )
