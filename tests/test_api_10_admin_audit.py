from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_admin_audit_repository_applies_server_scope_and_filters(
    monkeypatch,
):
    from sqlalchemy import true
    from sqlalchemy.dialects import postgresql

    import database.repositories.moderation as module
    from database.repositories.moderation import (
        ModerationRepository,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    actor_user_id = uuid4()
    target_id = uuid4()
    date_from = datetime(
        2026,
        9,
        1,
        tzinfo=UTC,
    )
    date_to = datetime(
        2026,
        9,
        8,
        tzinfo=UTC,
    )
    scope_calls = []

    class FakeScopeContext:
        def sql_predicate(
            self,
            **_kwargs,
        ):
            return true()

    class FakeScopeRepository:
        def __init__(
            self,
            session,
        ):
            self.session = session

        async def get_context(
            self,
            **kwargs,
        ):
            scope_calls.append(kwargs)
            return FakeScopeContext()

    class FakeResult:
        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(
            self,
            statement,
        ):
            self.statements.append(statement)
            return FakeResult()

    monkeypatch.setattr(
        module,
        "AdminScopeRepository",
        FakeScopeRepository,
    )

    session = FakeSession()
    repository = ModerationRepository(session)
    repository.require_admin_role = AsyncMock()

    result = await (
        repository.list_admin_audit_actions(
            admin_user_id=admin_user_id,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actions={"complaint_resolved"},
            target_types={"complaint"},
            target_id=target_id,
            date_from=date_from,
            date_to=date_to,
            limit=21,
            offset=0,
        )
    )

    assert result == []
    repository.require_admin_role.assert_awaited_once()
    assert scope_calls == [
        {
            "admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
        }
    ]

    statement = session.statements[-1]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )

    assert (
        "admin_audit_records.actor_user_id"
        in sql
    )
    assert "admin_audit_records.action IN" in sql
    assert (
        "admin_audit_records.target_type IN"
        in sql
    )
    assert (
        "admin_audit_records.target_id ="
        in sql
    )
    assert (
        "admin_audit_records.created_at >="
        in sql
    )
    assert (
        "admin_audit_records.created_at <="
        in sql
    )

    assert str(tenant_id) in sql
    assert str(actor_user_id) in sql
    assert str(target_id) in sql


@pytest.mark.asyncio
async def test_admin_audit_service_uses_actor_scope_and_maps_safe_views():
    from types import SimpleNamespace

    from services.api_admin_audit import (
        ApiAdminAuditPage,
        ApiAdminAuditService,
        ApiAdminAuditView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    filtered_actor_id = uuid4()
    target_id = uuid4()
    action_id = uuid4()
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
            "admin.logs.read",
        ),
    )

    card = SimpleNamespace(
        action_id=action_id,
        date="2026-09-07 18:00",
        actor="user-a1b2c3d4",
        action="complaint_resolved",
        target="complaint-e5f6a7b8",
        target_type="complaint",
        reason="Reviewed.",
        source="admin_action",
        tenant_id=uuid4(),
        actor_user_id=uuid4(),
        target_id=uuid4(),
    )

    class FakeModeration:
        async def open_admin_audit(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                items=(card,),
                page=0,
                target_type="complaint",
                has_next=False,
            )

    service = ApiAdminAuditService(
        moderation=FakeModeration(),
    )

    result = await service.list_audit(
        actor=actor,
        actor_user_id=filtered_actor_id,
        actions=("complaint_resolved",),
        target_types=("complaint",),
        target_id=target_id,
        date_from=datetime(
            2026,
            9,
            1,
            tzinfo=UTC,
        ),
        date_to=datetime(
            2026,
            9,
            8,
            tzinfo=UTC,
        ),
        page=0,
        page_size=20,
    )

    assert isinstance(result, ApiAdminAuditPage)
    assert isinstance(
        result.items[0],
        ApiAdminAuditView,
    )
    assert calls == [
        {
            "admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "actor_user_id": filtered_actor_id,
            "actions": {
                "complaint_resolved",
            },
            "target_types": {
                "complaint",
            },
            "target_id": target_id,
            "date_from": datetime(
                2026,
                9,
                1,
                tzinfo=UTC,
            ),
            "date_to": datetime(
                2026,
                9,
                8,
                tzinfo=UTC,
            ),
            "page": 0,
            "page_size": 20,
        }
    ]

    item = result.items[0]
    assert item.id == action_id
    assert item.actor == "user-a1b2c3d4"
    assert item.action == "complaint_resolved"
    assert item.target_type == "complaint"
    assert item.source == "admin_action"
    assert not hasattr(item, "tenant_id")
    assert not hasattr(item, "actor_user_id")
    assert not hasattr(item, "target_id")


@pytest.mark.asyncio
async def test_moderation_audit_forwards_release_filters():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from services.moderation import (
        ModerationService,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    actor_user_id = uuid4()
    target_id = uuid4()
    action_id = uuid4()
    calls = []

    date_from = datetime(
        2026,
        9,
        1,
        tzinfo=UTC,
    )
    date_to = datetime(
        2026,
        9,
        8,
        tzinfo=UTC,
    )

    row = SimpleNamespace(
        action_id=action_id,
        actor_user_id=actor_user_id,
        action="complaint_resolved",
        target_type="complaint",
        target_id=target_id,
        reason="Reviewed.",
        created_at=datetime(
            2026,
            9,
            7,
            18,
            0,
            tzinfo=UTC,
        ),
        source="admin_action",
    )

    class FakeSession:
        def __init__(self):
            self.commit = AsyncMock()
            self.rollback = AsyncMock()

    class FakeRepository:
        def __init__(self):
            self.session = FakeSession()

        async def list_admin_audit_actions(
            self,
            **kwargs,
        ):
            calls.append(("list", kwargs))
            return [row]

        async def log_event(
            self,
            **kwargs,
        ):
            calls.append(("audit", kwargs))

    repository = FakeRepository()
    service = ModerationService(repository)

    result = await service.open_admin_audit(
        admin_user_id=admin_user_id,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actions={"complaint_resolved"},
        target_types={"complaint"},
        target_id=target_id,
        date_from=date_from,
        date_to=date_to,
        page=0,
        page_size=20,
    )

    assert calls[0] == (
        "list",
        {
            "admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "target_types": {
                "complaint",
            },
            "limit": 21,
            "offset": 0,
            "actor_user_id": actor_user_id,
            "actions": {
                "complaint_resolved",
            },
            "target_id": target_id,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    assert result.items[0].action_id == action_id
    assert result.items[0].actor == (
        f"user-{actor_user_id.hex[:8]}"
    )
    assert result.items[0].target == (
        f"complaint-{target_id.hex[:8]}"
    )
    assert result.has_next is False
    repository.session.commit.assert_awaited_once()


def test_admin_audit_dependency_builds_scoped_service():
    from api.dependencies import (
        get_api_admin_audit_service,
    )
    from services.api_admin_audit import (
        ApiAdminAuditService,
    )

    session = object()

    service = get_api_admin_audit_service(
        session=session,
    )

    assert isinstance(
        service,
        ApiAdminAuditService,
    )
    assert (
        service.moderation.repository.session
        is session
    )


@pytest.mark.asyncio
async def test_admin_audit_endpoint_uses_current_actor_and_release_filters():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_audit_service,
    )
    from services.api_admin_audit import (
        ApiAdminAuditPage,
        ApiAdminAuditView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    filtered_actor_id = uuid4()
    target_id = uuid4()
    action_id = uuid4()
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
            "admin.logs.read",
        ),
    )

    class FakeService:
        async def list_audit(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiAdminAuditPage(
                items=(
                    ApiAdminAuditView(
                        id=action_id,
                        date="2026-09-07 18:00",
                        actor="user-a1b2c3d4",
                        action=(
                            "complaint_resolved"
                        ),
                        target=(
                            "complaint-e5f6a7b8"
                        ),
                        target_type="complaint",
                        reason="Reviewed.",
                        source="admin_action",
                    ),
                ),
                page=0,
                has_next=False,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_audit_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/admin/audit",
            params=[
                (
                    "actor_id",
                    str(filtered_actor_id),
                ),
                (
                    "action",
                    "complaint_resolved",
                ),
                (
                    "object_type",
                    "complaint",
                ),
                (
                    "object_id",
                    str(target_id),
                ),
                (
                    "date_from",
                    "2026-09-01T00:00:00Z",
                ),
                (
                    "date_to",
                    "2026-09-08T00:00:00Z",
                ),
                ("limit", "20"),
            ],
            headers={
                "X-Request-ID": (
                    "admin-audit-list"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "actor": actor,
            "actor_user_id": (
                filtered_actor_id
            ),
            "actions": (
                "complaint_resolved",
            ),
            "target_types": (
                "complaint",
            ),
            "target_id": target_id,
            "date_from": datetime(
                2026,
                9,
                1,
                tzinfo=UTC,
            ),
            "date_to": datetime(
                2026,
                9,
                8,
                tzinfo=UTC,
            ),
            "page": 0,
            "page_size": 20,
        }
    ]

    payload = response.json()
    assert payload["request_id"] == (
        "admin-audit-list"
    )
    assert payload["meta"] == {
        "next_cursor": None,
        "has_more": False,
    }

    item = payload["data"][0]
    assert item["id"] == str(action_id)
    assert item["actor"] == "user-a1b2c3d4"
    assert item["action"] == (
        "complaint_resolved"
    )
    assert item["target"] == (
        "complaint-e5f6a7b8"
    )
    assert "tenant_id" not in item
    assert "actor_user_id" not in item
    assert "target_id" not in item


def test_admin_audit_openapi_is_read_only_and_tenant_neutral():
    from api.app import create_app

    schema = create_app().openapi()
    path = schema["paths"][
        "/api/v1/admin/audit"
    ]

    assert set(path) == {"get"}

    parameters = {
        parameter["name"]
        for parameter in path["get"][
            "parameters"
        ]
    }

    assert {
        "actor_id",
        "action",
        "object_type",
        "object_id",
        "date_from",
        "date_to",
        "limit",
        "cursor",
    } <= parameters

    assert "tenant_id" not in parameters
