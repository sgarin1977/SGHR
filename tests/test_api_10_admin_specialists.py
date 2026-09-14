from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_admin_repository_lists_specialists_by_server_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.admin_scope import (
        AdminScopeContext,
    )
    from database.repositories.api_admin import (
        AdminApiRepository,
    )

    tenant_id = uuid4()
    country_id = uuid4()
    admin_user_id = uuid4()
    expected = [
        object(),
        object(),
    ]

    class FakeResult:
        def all(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    scope_context = AdminScopeContext(
        admin_user_id=admin_user_id,
        tenant_id=tenant_id,
        is_global=False,
        country_ids=frozenset(
            {country_id}
        ),
        language_codes=frozenset(
            {"uk"}
        ),
    )

    session = FakeSession()
    repository = AdminApiRepository(session)

    result = await (
        repository.list_specialists(
            scope_context=scope_context,
            limit=21,
            offset=0,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "FROM specialists" in sql
    assert "JOIN users" in sql
    assert (
        "LEFT OUTER JOIN "
        "professional_cabinets"
    ) in sql

    assert "specialists.tenant_id" in sql
    assert "users.tenant_id" in sql
    assert str(tenant_id) in sql

    assert "coalesce(" in sql.lower()
    assert "specialists.country_id" in sql
    assert (
        "professional_cabinets.country_id"
        in sql
    )
    assert "users.country_id" in sql
    assert str(country_id) in sql

    assert "lower(users.language_code)" in sql
    assert "'uk'" in sql
    assert "LIMIT 21" in sql
    assert "OFFSET 0" in sql



@pytest.mark.asyncio
async def test_admin_repository_gets_specialist_by_server_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.admin_scope import (
        AdminScopeContext,
    )
    from database.repositories.api_admin import (
        AdminApiRepository,
    )

    tenant_id = uuid4()
    country_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    expected = object()

    class FakeResult:
        def one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    scope_context = AdminScopeContext(
        admin_user_id=admin_user_id,
        tenant_id=tenant_id,
        is_global=False,
        country_ids=frozenset(
            {country_id}
        ),
        language_codes=frozenset(
            {"uk"}
        ),
    )

    session = FakeSession()
    repository = AdminApiRepository(session)

    result = await repository.get_specialist(
        scope_context=scope_context,
        specialist_id=specialist_id,
    )

    assert result is expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert "FROM specialists" in sql
    assert "JOIN users" in sql
    assert (
        "LEFT OUTER JOIN "
        "professional_cabinets"
    ) in sql

    assert "specialists.id" in sql
    assert str(specialist_id) in sql
    assert "specialists.tenant_id" in sql
    assert "users.tenant_id" in sql
    assert str(tenant_id) in sql

    assert "coalesce(" in sql.lower()
    assert str(country_id) in sql
    assert "lower(users.language_code)" in sql
    assert "'uk'" in sql
    assert "LIMIT 1" in sql



@pytest.mark.asyncio
async def test_admin_specialists_service_uses_current_actor_scope():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_admin_specialists import (
        ApiAdminSpecialistPage,
        ApiAdminSpecialistView,
        ApiAdminSpecialistsService,
    )
    from services.api_identity import (
        ApiActorContext,
        ApiRoleScopeContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    specialist_user_id = uuid4()
    cabinet_id = uuid4()
    country_id = uuid4()
    city_id = uuid4()
    category_id = uuid4()
    profession_id = uuid4()
    created_at = datetime(
        2026,
        9,
        4,
        23,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.specialists.read",
        ),
        role_scopes=(
            ApiRoleScopeContext(
                role="moderator",
                scope_type="country",
                scope_id=country_id,
                scope_code=None,
            ),
        ),
    )

    specialist = SimpleNamespace(
        id=specialist_id,
        tenant_id=tenant_id,
        user_id=specialist_user_id,
        active_professional_cabinet_id=(
            cabinet_id
        ),
        category_id=category_id,
        profession_id=profession_id,
        country_id=None,
        city_id=None,
        display_name="Test Specialist",
        status="pending",
        is_verified=False,
        rating=4.5,
        reviews_count=8,
        created_at=created_at,
        extra_metadata={
            "private": "must-not-leak",
        },
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
        country_id=country_id,
        city_id=city_id,
        availability_status="available",
        moderation_status="pending",
        extra_metadata={
            "private": "must-not-leak",
        },
    )

    class FakeRepository:
        async def list_specialists(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return [
                (specialist, cabinet),
                (specialist, cabinet),
                (specialist, cabinet),
            ]

    service = ApiAdminSpecialistsService(
        repository=FakeRepository(),
    )

    result = await service.list_specialists(
        actor=actor,
        page=1,
        page_size=2,
    )

    assert isinstance(
        result,
        ApiAdminSpecialistPage,
    )
    assert len(result.items) == 2
    assert result.page == 1
    assert result.has_next is True

    item = result.items[0]
    assert isinstance(
        item,
        ApiAdminSpecialistView,
    )
    assert item.id == specialist_id
    assert item.user_id == specialist_user_id
    assert (
        item.professional_cabinet_id
        == cabinet_id
    )
    assert item.display_name == (
        "Test Specialist"
    )
    assert item.country_id == country_id
    assert item.city_id == city_id
    assert item.status == "pending"
    assert item.moderation_status == (
        "pending"
    )

    assert not hasattr(item, "tenant_id")
    assert not hasattr(item, "metadata")
    assert not hasattr(
        item,
        "extra_metadata",
    )

    assert len(calls) == 1
    scope_context = calls[0][
        "scope_context"
    ]
    assert (
        scope_context.admin_user_id
        == admin_user_id
    )
    assert scope_context.tenant_id == tenant_id
    assert scope_context.is_global is False
    assert scope_context.country_ids == (
        frozenset({country_id})
    )
    assert calls[0]["limit"] == 3
    assert calls[0]["offset"] == 2



@pytest.mark.asyncio
async def test_admin_specialist_detail_uses_current_actor_scope():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_admin_specialists import (
        ApiAdminSpecialistView,
        ApiAdminSpecialistsService,
    )
    from services.api_identity import (
        ApiActorContext,
        ApiRoleScopeContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    specialist_user_id = uuid4()
    cabinet_id = uuid4()
    country_id = uuid4()
    category_id = uuid4()
    profession_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        0,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.specialists.read",
        ),
        role_scopes=(
            ApiRoleScopeContext(
                role="admin",
                scope_type="country",
                scope_id=country_id,
                scope_code=None,
            ),
        ),
    )

    specialist = SimpleNamespace(
        id=specialist_id,
        tenant_id=tenant_id,
        user_id=specialist_user_id,
        active_professional_cabinet_id=(
            cabinet_id
        ),
        category_id=category_id,
        profession_id=profession_id,
        country_id=country_id,
        city_id=None,
        display_name="Test Specialist",
        status="active",
        is_verified=True,
        rating=4.9,
        reviews_count=20,
        created_at=created_at,
        extra_metadata={
            "private": "must-not-leak",
        },
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
        tenant_id=tenant_id,
        country_id=country_id,
        city_id=None,
        availability_status="available",
        moderation_status="approved",
        extra_metadata={
            "private": "must-not-leak",
        },
    )

    class FakeRepository:
        async def get_specialist(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return specialist, cabinet

    service = ApiAdminSpecialistsService(
        repository=FakeRepository(),
    )

    result = await service.get_specialist(
        actor=actor,
        specialist_id=specialist_id,
    )

    assert isinstance(
        result,
        ApiAdminSpecialistView,
    )
    assert result.id == specialist_id
    assert result.user_id == (
        specialist_user_id
    )
    assert result.moderation_status == (
        "approved"
    )
    assert not hasattr(result, "tenant_id")
    assert not hasattr(result, "metadata")

    assert len(calls) == 1
    assert calls[0]["specialist_id"] == (
        specialist_id
    )

    scope_context = calls[0][
        "scope_context"
    ]
    assert (
        scope_context.admin_user_id
        == admin_user_id
    )
    assert scope_context.tenant_id == tenant_id
    assert scope_context.country_ids == (
        frozenset({country_id})
    )



@pytest.mark.asyncio
async def test_admin_specialist_read_guard_accepts_existing_permissions():
    from api.auth import (
        require_admin_any_permission,
    )
    from api.errors import ApiHttpError
    from services.api_identity import (
        ApiActorContext,
    )

    permissions = (
        "admin.specialists.moderate",
        "moderation.specialists.view",
    )
    guard = require_admin_any_permission(
        *permissions
    )

    def make_actor(
        *,
        role,
        actor_permissions,
    ):
        return ApiActorContext(
            user_id=uuid4(),
            tenant_id=uuid4(),
            active_role=role,
            roles=(role,),
            language_code="uk",
            timezone="Europe/Kyiv",
            status="active",
            permissions=actor_permissions,
        )

    admin = make_actor(
        role="admin",
        actor_permissions=(
            "admin.specialists.moderate",
        ),
    )
    moderator = make_actor(
        role="moderator",
        actor_permissions=(
            "moderation.specialists.view",
        ),
    )

    assert await guard(admin) is admin
    assert await guard(moderator) is moderator

    without_permission = make_actor(
        role="admin",
        actor_permissions=(),
    )
    with pytest.raises(
        ApiHttpError
    ) as permission_error:
        await guard(without_permission)

    assert (
        permission_error.value.status_code
        == 403
    )
    assert permission_error.value.code == (
        "permission_required"
    )

    client = make_actor(
        role="client",
        actor_permissions=(
            "moderation.specialists.view",
        ),
    )
    with pytest.raises(
        ApiHttpError
    ) as role_error:
        await guard(client)

    assert role_error.value.status_code == 403
    assert role_error.value.code == (
        "admin_role_required"
    )



@pytest.mark.asyncio
async def test_admin_specialists_endpoint_uses_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_specialists_service,
    )
    from services.api_admin_specialists import (
        ApiAdminSpecialistPage,
        ApiAdminSpecialistView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    specialist_user_id = uuid4()
    cabinet_id = uuid4()
    category_id = uuid4()
    profession_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        1,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "moderation.specialists.view",
        ),
    )

    item = ApiAdminSpecialistView(
        id=specialist_id,
        user_id=specialist_user_id,
        professional_cabinet_id=cabinet_id,
        category_id=category_id,
        profession_id=profession_id,
        country_id=None,
        city_id=None,
        display_name="Test Specialist",
        status="pending",
        moderation_status="pending",
        is_verified=False,
        availability_status="available",
        rating=4.5,
        reviews_count=8,
        created_at=created_at,
    )

    class FakeService:
        async def list_specialists(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiAdminSpecialistPage(
                items=(item,),
                page=0,
                has_next=False,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_specialists_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/admin/specialists",
            params={
                "limit": 20,
            },
            headers={
                "X-Request-ID": (
                    "admin-specialists-list"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["data"][0]["id"] == (
        str(specialist_id)
    )
    assert "tenant_id" not in (
        payload["data"][0]
    )
    assert calls == [
        {
            "actor": actor,
            "page": 0,
            "page_size": 20,
        }
    ]

    parameters = application.openapi()[
        "paths"
    ][
        "/api/v1/admin/specialists"
    ]["get"].get("parameters", [])

    assert all(
        parameter["name"] != "tenant_id"
        for parameter in parameters
    )



@pytest.mark.asyncio
async def test_admin_specialist_detail_endpoint_uses_current_actor():
    from datetime import UTC, datetime

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_specialists_service,
    )
    from services.api_admin_specialists import (
        ApiAdminSpecialistView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    specialist_user_id = uuid4()
    cabinet_id = uuid4()
    category_id = uuid4()
    profession_id = uuid4()
    created_at = datetime(
        2026,
        9,
        5,
        2,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.specialists.moderate",
        ),
    )

    item = ApiAdminSpecialistView(
        id=specialist_id,
        user_id=specialist_user_id,
        professional_cabinet_id=cabinet_id,
        category_id=category_id,
        profession_id=profession_id,
        country_id=None,
        city_id=None,
        display_name="Test Specialist",
        status="active",
        moderation_status="approved",
        is_verified=True,
        availability_status="available",
        rating=4.9,
        reviews_count=20,
        created_at=created_at,
    )

    class FakeService:
        async def get_specialist(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return item

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_specialists_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/admin/specialists/"
                f"{specialist_id}"
            ),
            headers={
                "X-Request-ID": (
                    "admin-specialist-detail"
                ),
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["data"]["id"] == (
        str(specialist_id)
    )
    assert "tenant_id" not in payload["data"]
    assert calls == [
        {
            "actor": actor,
            "specialist_id": specialist_id,
        }
    ]



@pytest.mark.asyncio
async def test_admin_specialist_approve_reuses_existing_moderation_flow():
    from types import SimpleNamespace

    from services.api_admin_specialists import (
        ApiAdminSpecialistsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "moderation.specialists.approve",
        ),
    )

    expected = SimpleNamespace(
        entity_id=cabinet_id,
        status="approved",
        message=(
            "Professional cabinet approved."
        ),
    )

    class FakeModeration:
        async def approve_specialist(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = ApiAdminSpecialistsService(
        repository=object(),
        moderation=FakeModeration(),
    )

    result = await service.approve_specialist(
        actor=actor,
        specialist_id=specialist_id,
        reason="Verified profile.",
    )

    assert result is expected
    assert calls == [
        {
            "admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "reason": "Verified profile.",
            "specialist_id": specialist_id,
            "professional_cabinet_id": None,
        }
    ]



@pytest.mark.asyncio
async def test_admin_specialist_reject_reuses_existing_moderation_flow():
    from types import SimpleNamespace

    from services.api_admin_specialists import (
        ApiAdminSpecialistsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "moderation.specialists.reject",
        ),
    )

    expected = SimpleNamespace(
        entity_id=cabinet_id,
        status="rejected",
        message=(
            "Professional cabinet rejected."
        ),
    )

    class FakeModeration:
        async def reject_specialist(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return expected

    service = ApiAdminSpecialistsService(
        repository=object(),
        moderation=FakeModeration(),
    )

    result = await service.reject_specialist(
        actor=actor,
        specialist_id=specialist_id,
        reason="Profile data is invalid.",
    )

    assert result is expected
    assert calls == [
        {
            "admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "reason": (
                "Profile data is invalid."
            ),
            "specialist_id": specialist_id,
            "professional_cabinet_id": None,
        }
    ]



@pytest.mark.asyncio
async def test_admin_specialist_visibility_uses_scoped_existing_flow():
    from types import SimpleNamespace

    from services.api_admin_specialists import (
        ApiAdminSpecialistsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.specialists.moderate",
        ),
    )

    specialist = SimpleNamespace(
        id=specialist_id,
        active_professional_cabinet_id=(
            cabinet_id
        ),
    )
    cabinet = SimpleNamespace(
        id=cabinet_id,
    )

    class FakeRepository:
        async def get_specialist(
            self,
            **kwargs,
        ):
            calls.append(
                ("scope", kwargs)
            )
            return specialist, cabinet

    class FakeModeration:
        async def hide_professional_cabinet(
            self,
            **kwargs,
        ):
            calls.append(
                ("hide", kwargs)
            )
            return SimpleNamespace(
                entity_id=cabinet_id,
                status="hidden",
                message=(
                    "Professional cabinet hidden."
                ),
            )

        async def restore_professional_cabinet(
            self,
            **kwargs,
        ):
            calls.append(
                ("unhide", kwargs)
            )
            return SimpleNamespace(
                entity_id=cabinet_id,
                status="approved",
                message=(
                    "Professional cabinet restored."
                ),
            )

    service = ApiAdminSpecialistsService(
        repository=FakeRepository(),
        moderation=FakeModeration(),
    )

    hidden = await service.hide_specialist(
        actor=actor,
        specialist_id=specialist_id,
        reason="Moderation decision.",
    )
    restored = await service.unhide_specialist(
        actor=actor,
        specialist_id=specialist_id,
        reason="Issue resolved.",
    )

    assert hidden.status == "hidden"
    assert restored.status == "approved"

    scope_calls = [
        value
        for kind, value in calls
        if kind == "scope"
    ]
    assert len(scope_calls) == 2

    for scope_call in scope_calls:
        assert (
            scope_call["specialist_id"]
            == specialist_id
        )
        assert (
            scope_call["scope_context"]
            .tenant_id
            == tenant_id
        )
        assert (
            scope_call["scope_context"]
            .admin_user_id
            == admin_user_id
        )

    assert (
        "hide",
        {
            "admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "reason": (
                "Moderation decision."
            ),
        },
    ) in calls

    assert (
        "unhide",
        {
            "admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "professional_cabinet_id": (
                cabinet_id
            ),
            "reason": "Issue resolved.",
        },
    ) in calls



@pytest.mark.asyncio
async def test_admin_specialist_moderation_endpoints_use_current_actor():
    from types import SimpleNamespace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_specialists_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.specialists.moderate",
        ),
    )

    class FakeService:
        async def approve_specialist(
            self,
            **kwargs,
        ):
            calls.append(("approve", kwargs))
            return SimpleNamespace(
                entity_id=cabinet_id,
                status="approved",
                message="Approved.",
            )

        async def reject_specialist(
            self,
            **kwargs,
        ):
            calls.append(("reject", kwargs))
            return SimpleNamespace(
                entity_id=cabinet_id,
                status="rejected",
                message="Rejected.",
            )

        async def hide_specialist(
            self,
            **kwargs,
        ):
            calls.append(("hide", kwargs))
            return SimpleNamespace(
                entity_id=cabinet_id,
                status="hidden",
                message="Hidden.",
            )

        async def unhide_specialist(
            self,
            **kwargs,
        ):
            calls.append(("unhide", kwargs))
            return SimpleNamespace(
                entity_id=cabinet_id,
                status="approved",
                message="Restored.",
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_specialists_service
    ] = lambda: FakeService()

    cases = (
        ("approve", "approved"),
        ("reject", "rejected"),
        ("hide", "hidden"),
        ("unhide", "approved"),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        for action, expected_status in cases:
            response = await client.post(
                (
                    "/api/v1/admin/specialists/"
                    f"{specialist_id}/{action}"
                ),
                json={
                    "reason": (
                        "Documented moderation "
                        "decision."
                    ),
                },
                headers={
                    "X-Request-ID": (
                        f"admin-specialist-{action}"
                    ),
                },
            )

            assert response.status_code == 200
            payload = response.json()
            assert payload["data"]["status"] == (
                expected_status
            )
            assert payload["data"][
                "entity_id"
            ] == str(cabinet_id)

    assert calls == [
        (
            action,
            {
                "actor": actor,
                "specialist_id": specialist_id,
                "reason": (
                    "Documented moderation "
                    "decision."
                ),
            },
        )
        for action, _status in cases
    ]
