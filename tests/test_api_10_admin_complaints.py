from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_admin_complaint_list_uses_current_actor_scope():
    from services.api_admin_complaints import (
        ApiAdminComplaintPage,
        ApiAdminComplaintView,
        ApiAdminComplaintsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    created_at = datetime(
        2026,
        9,
        7,
        14,
        0,
        tzinfo=UTC,
    )
    calls = []

    cards = [
        SimpleNamespace(
            complaint_id=uuid4(),
            reporter_label=f"user-{index}",
            target_label=f"target-{index}",
            reason="spam",
            status="new",
            created_at=created_at,
            is_assigned=False,
            has_conversation_context=True,
            requires_admin_escalation=False,
        )
        for index in range(3)
    ]

    class FakeModeration:
        async def open_complaints_queue(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return cards

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.complaints.manage",
        ),
    )
    service = ApiAdminComplaintsService(
        moderation=FakeModeration(),
    )

    result = await service.list_complaints(
        actor=actor,
        statuses=("new", "in_review"),
        page=1,
        page_size=2,
    )

    assert isinstance(
        result,
        ApiAdminComplaintPage,
    )
    assert len(result.items) == 2
    assert all(
        isinstance(item, ApiAdminComplaintView)
        for item in result.items
    )
    assert result.page == 1
    assert result.has_next is True

    assert calls == [
        {
            "moderator_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "statuses": {
                "new",
                "in_review",
            },
            "page": 1,
            "page_size": 2,
        }
    ]

    item = result.items[0]
    assert item.id == cards[0].complaint_id
    assert item.reporter_label == "user-0"
    assert item.target_label == "target-0"
    assert item.reason == "spam"
    assert item.status == "new"
    assert item.created_at == created_at

    assert not hasattr(item, "tenant_id")
    assert not hasattr(item, "reporter_user_id")
    assert not hasattr(item, "target_id")


@pytest.mark.asyncio
async def test_admin_complaint_detail_uses_current_actor_scope():
    from services.api_admin_complaints import (
        ApiAdminComplaintDetailView,
        ApiAdminComplaintsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    complaint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        7,
        15,
        0,
        tzinfo=UTC,
    )
    calls = []

    card = SimpleNamespace(
        complaint_id=complaint_id,
        reporter_label="user-a1b2c3d4",
        target_type="specialist",
        target_label="Specialist profile",
        reason="misleading_information",
        comment="Profile data is incorrect.",
        status="in_review",
        created_at=created_at,
        has_conversation_context=False,
        requires_admin_escalation=False,
        history=(
            "complaint_created",
            "complaint_taken",
        ),
        reporter_user_id=uuid4(),
        target_id=uuid4(),
        tenant_id=uuid4(),
    )

    class FakeModeration:
        async def get_moderator_complaint_card(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return card

    actor = ApiActorContext(
        user_id=admin_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.complaints.manage",
        ),
    )
    service = ApiAdminComplaintsService(
        moderation=FakeModeration(),
    )

    result = await service.get_complaint(
        actor=actor,
        complaint_id=complaint_id,
    )

    assert isinstance(
        result,
        ApiAdminComplaintDetailView,
    )
    assert calls == [
        {
            "moderator_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "complaint_id": complaint_id,
        }
    ]
    assert result.id == complaint_id
    assert result.reporter_label == (
        "user-a1b2c3d4"
    )
    assert result.target_type == "specialist"
    assert result.target_label == (
        "Specialist profile"
    )
    assert result.comment == (
        "Profile data is incorrect."
    )
    assert result.history == (
        "complaint_created",
        "complaint_taken",
    )

    assert not hasattr(result, "tenant_id")
    assert not hasattr(
        result,
        "reporter_user_id",
    )
    assert not hasattr(result, "target_id")


def test_admin_complaints_dependency_builds_scoped_service():
    from api.dependencies import (
        get_api_admin_complaints_service,
    )
    from services.api_admin_complaints import (
        ApiAdminComplaintsService,
    )

    session = object()

    service = (
        get_api_admin_complaints_service(
            session=session,
        )
    )

    assert isinstance(
        service,
        ApiAdminComplaintsService,
    )
    assert (
        service.moderation.repository.session
        is session
    )


@pytest.mark.asyncio
async def test_admin_complaints_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_complaints_service,
    )
    from services.api_admin_complaints import (
        ApiAdminComplaintPage,
        ApiAdminComplaintView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    complaint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        7,
        16,
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
            "admin.complaints.manage",
        ),
    )

    class FakeService:
        async def list_complaints(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiAdminComplaintPage(
                items=(
                    ApiAdminComplaintView(
                        id=complaint_id,
                        reporter_label=(
                            "user-a1b2c3d4"
                        ),
                        target_label=(
                            "Specialist profile"
                        ),
                        reason="spam",
                        status="new",
                        created_at=created_at,
                        is_assigned=False,
                        has_conversation_context=True,
                        requires_admin_escalation=False,
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
        get_api_admin_complaints_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/admin/complaints",
            params={
                "status": "new",
                "limit": 20,
            },
            headers={
                "X-Request-ID": (
                    "admin-complaints-list"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "actor": actor,
            "statuses": ("new",),
            "page": 0,
            "page_size": 20,
        }
    ]

    payload = response.json()
    assert payload["request_id"] == (
        "admin-complaints-list"
    )
    assert payload["meta"] == {
        "next_cursor": None,
        "has_more": False,
    }

    item = payload["data"][0]
    assert item["id"] == str(complaint_id)
    assert item["reporter_label"] == (
        "user-a1b2c3d4"
    )
    assert item["status"] == "new"
    assert "tenant_id" not in item
    assert "reporter_user_id" not in item
    assert "target_id" not in item


@pytest.mark.asyncio
async def test_admin_complaint_detail_endpoint_uses_current_actor():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_complaints_service,
    )
    from services.api_admin_complaints import (
        ApiAdminComplaintDetailView,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    complaint_id = uuid4()
    created_at = datetime(
        2026,
        9,
        7,
        17,
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
            "admin.complaints.manage",
        ),
    )

    class FakeService:
        async def get_complaint(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return ApiAdminComplaintDetailView(
                id=complaint_id,
                reporter_label=(
                    "user-a1b2c3d4"
                ),
                target_type="specialist",
                target_label=(
                    "Specialist profile"
                ),
                reason="spam",
                comment="Misleading profile.",
                status="in_review",
                created_at=created_at,
                has_conversation_context=True,
                requires_admin_escalation=False,
                history=(
                    "complaint_created",
                    "complaint_taken",
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_complaints_service
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
                "/api/v1/admin/complaints/"
                f"{complaint_id}"
            ),
            headers={
                "X-Request-ID": (
                    "admin-complaint-detail"
                ),
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "actor": actor,
            "complaint_id": complaint_id,
        }
    ]

    payload = response.json()
    assert payload["request_id"] == (
        "admin-complaint-detail"
    )
    assert payload["meta"] == {}

    item = payload["data"]
    assert item["id"] == str(complaint_id)
    assert item["target_type"] == "specialist"
    assert item["comment"] == (
        "Misleading profile."
    )
    assert item["history"] == [
        "complaint_created",
        "complaint_taken",
    ]
    assert "tenant_id" not in item
    assert "reporter_user_id" not in item
    assert "target_id" not in item


@pytest.mark.asyncio
async def test_admin_complaint_take_reuses_existing_scoped_flow():
    from services.api_admin_complaints import (
        ApiAdminComplaintActionResult,
        ApiAdminComplaintsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    complaint_id = uuid4()
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
            "admin.complaints.manage",
        ),
    )

    class FakeModeration:
        async def take_complaint(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                entity_id=complaint_id,
                status="in_review",
                message="Complaint taken.",
            )

    service = ApiAdminComplaintsService(
        moderation=FakeModeration(),
    )

    result = await service.take_complaint(
        actor=actor,
        complaint_id=complaint_id,
    )

    assert isinstance(
        result,
        ApiAdminComplaintActionResult,
    )
    assert calls == [
        {
            "moderator_user_id": (
                admin_user_id
            ),
            "tenant_id": tenant_id,
            "complaint_id": complaint_id,
        }
    ]
    assert result.complaint_id == complaint_id
    assert result.status == "in_review"
    assert result.message == "Complaint taken."
    assert not hasattr(result, "tenant_id")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    (
        "resolved",
        "rejected",
    ),
)
async def test_admin_complaint_resolution_reuses_existing_scoped_flow(
    status,
):
    from services.api_admin_complaints import (
        ApiAdminComplaintActionResult,
        ApiAdminComplaintsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    complaint_id = uuid4()
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
            "admin.complaints.manage",
        ),
    )

    class FakeModeration:
        async def resolve_complaint(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                entity_id=complaint_id,
                status=status,
                message="Complaint updated.",
            )

    service = ApiAdminComplaintsService(
        moderation=FakeModeration(),
    )

    result = await service.resolve_complaint(
        actor=actor,
        complaint_id=complaint_id,
        status=status,
        reason="Reviewed by administrator.",
    )

    assert isinstance(
        result,
        ApiAdminComplaintActionResult,
    )
    assert calls == [
        {
            "admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "complaint_id": complaint_id,
            "status": status,
            "reason": (
                "Reviewed by administrator."
            ),
        }
    ]
    assert result.complaint_id == complaint_id
    assert result.status == status
    assert result.message == (
        "Complaint updated."
    )


@pytest.mark.asyncio
async def test_admin_complaint_escalation_reuses_existing_scoped_flow():
    from services.api_admin_complaints import (
        ApiAdminComplaintActionResult,
        ApiAdminComplaintsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    moderator_user_id = uuid4()
    complaint_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=moderator_user_id,
        tenant_id=tenant_id,
        active_role="moderator",
        roles=("moderator",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "moderation.complaints.resolve",
        ),
    )

    class FakeModeration:
        async def escalate_complaint_to_admin(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                entity_id=complaint_id,
                status="in_review",
                message=(
                    "Complaint escalated to Admin."
                ),
            )

    service = ApiAdminComplaintsService(
        moderation=FakeModeration(),
    )

    result = await service.escalate_complaint(
        actor=actor,
        complaint_id=complaint_id,
        reason="Requires administrator review.",
    )

    assert isinstance(
        result,
        ApiAdminComplaintActionResult,
    )
    assert calls == [
        {
            "moderator_user_id": (
                moderator_user_id
            ),
            "tenant_id": tenant_id,
            "complaint_id": complaint_id,
            "reason": (
                "Requires administrator review."
            ),
        }
    ]
    assert result.complaint_id == complaint_id
    assert result.status == "in_review"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_status"),
    (
        ("take", "in_review"),
        ("escalate", "in_review"),
        ("resolve", "resolved"),
        ("reject", "rejected"),
    ),
)
async def test_admin_complaint_moderation_endpoints_use_current_actor(
    action,
    expected_status,
):
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_complaints_service,
    )
    from services.api_admin_complaints import (
        ApiAdminComplaintActionResult,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    tenant_id = uuid4()
    admin_user_id = uuid4()
    complaint_id = uuid4()
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
            "admin.complaints.manage",
        ),
    )

    class FakeService:
        async def take_complaint(
            self,
            **kwargs,
        ):
            calls.append(("take", kwargs))
            return ApiAdminComplaintActionResult(
                complaint_id=complaint_id,
                status="in_review",
                message="Complaint taken.",
            )

        async def escalate_complaint(
            self,
            **kwargs,
        ):
            calls.append(("escalate", kwargs))
            return ApiAdminComplaintActionResult(
                complaint_id=complaint_id,
                status="in_review",
                message=(
                    "Complaint escalated to Admin."
                ),
            )

        async def resolve_complaint(
            self,
            **kwargs,
        ):
            calls.append(("resolve", kwargs))
            return ApiAdminComplaintActionResult(
                complaint_id=complaint_id,
                status=kwargs["status"],
                message="Complaint updated.",
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_complaints_service
    ] = lambda: FakeService()

    request_body = (
        {}
        if action == "take"
        else {
            "reason": (
                "Reviewed by administrator."
            ),
        }
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/admin/complaints/"
                f"{complaint_id}/{action}"
            ),
            json=request_body,
            headers={
                "X-Request-ID": (
                    f"admin-complaint-{action}"
                ),
            },
        )

    assert response.status_code == 200

    payload = response.json()
    assert payload["data"] == {
        "complaint_id": str(complaint_id),
        "status": expected_status,
        "message": payload["data"]["message"],
    }
    assert payload["request_id"] == (
        f"admin-complaint-{action}"
    )

    called_action, kwargs = calls[0]
    if action == "take":
        assert called_action == "take"
        assert kwargs == {
            "actor": actor,
            "complaint_id": complaint_id,
        }
    elif action == "escalate":
        assert called_action == "escalate"
        assert kwargs == {
            "actor": actor,
            "complaint_id": complaint_id,
            "reason": (
                "Reviewed by administrator."
            ),
        }
    else:
        assert called_action == "resolve"
        assert kwargs == {
            "actor": actor,
            "complaint_id": complaint_id,
            "status": expected_status,
            "reason": (
                "Reviewed by administrator."
            ),
        }


@pytest.mark.asyncio
async def test_admin_complaint_action_sanitizes_moderation_error():
    from services.api_admin_complaints import (
        ApiAdminComplaintOperationError,
        ApiAdminComplaintsService,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.moderation import (
        ModerationError,
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.complaints.manage",
        ),
    )

    class FakeModeration:
        async def take_complaint(
            self,
            **_kwargs,
        ):
            raise ModerationError(
                "Sensitive database details."
            )

    service = ApiAdminComplaintsService(
        moderation=FakeModeration(),
    )

    with pytest.raises(
        ApiAdminComplaintOperationError,
        match="Complaint operation failed",
    ):
        await service.take_complaint(
            actor=actor,
            complaint_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_admin_complaint_operation_error_is_sanitized():
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_admin_complaints_service,
    )
    from services.api_admin_complaints import (
        ApiAdminComplaintOperationError,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "admin.complaints.manage",
        ),
    )

    class FakeService:
        async def take_complaint(
            self,
            **_kwargs,
        ):
            raise ApiAdminComplaintOperationError(
                "Sensitive database details."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_api_admin_complaints_service
    ] = lambda: FakeService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/admin/complaints/"
                f"{uuid4()}/take"
            ),
            json={},
            headers={
                "X-Request-ID": (
                    "admin-complaint-error"
                ),
            },
        )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == {
        "code": (
            "complaint_moderation_failed"
        ),
        "message": (
            "Complaint moderation could not "
            "be completed."
        ),
        "request_id": (
            "admin-complaint-error"
        ),
    }
    assert "Sensitive" not in response.text
    assert "database" not in response.text
