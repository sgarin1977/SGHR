from construction_rate_limit_fixture import with_local_construction_rate_limit
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from services.files import FileService
from services.platform_company_profile_access import (
    CompanyProfilePermissionDeniedError,
    PLATFORM_ORGANIZATION_PROFILE_MANAGE,
)
import services.platform_company_profile_access as profile_access


@pytest.fixture
def completion_case(monkeypatch):
    actor = SimpleNamespace(
        user_id=UUID(int=1), tenant_id=UUID(int=2), status="active",
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,),
    )
    pending = SimpleNamespace(
        id=UUID(int=3), tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        entity_type="organization", entity_id=actor.tenant_id,
        storage_path="private-test-logo.png", mime_type="image/png",
        size_bytes=1024, status="pending_upload",
    )
    ready = SimpleNamespace(
        id=pending.id, status="ready", antivirus_status="not_scanned",
        mime_type="image/png", size_bytes=1024,
        completed_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    repository = SimpleNamespace(
        session=session, get_pending_upload_for_update=AsyncMock(return_value=pending),
        complete_upload=AsyncMock(return_value=ready),
    )
    storage = SimpleNamespace(inspect_object=AsyncMock(return_value=SimpleNamespace(
        exists=True, mime_type="image/png", size_bytes=1024, provider_metadata={},
    )))
    loader = AsyncMock(return_value=None)
    monkeypatch.setattr(profile_access, "load_company_profile_write_context", loader)
    return SimpleNamespace(
        actor=actor, pending=pending, repository=repository,
        session=session, storage=storage, loader=loader,
        service=FileService(repository=repository, storage=storage,
                            storage_provider_name="supabase"),
    )


async def complete(case, **overrides):
    args = dict(
        tenant_id=UUID(int=2), owner_user_id=UUID(int=1),
        file_id=UUID(int=3), actor=case.actor,
    )
    args.update(overrides)
    return await case.service.complete_upload(**args)


def assert_no_completion(case):
    case.storage.inspect_object.assert_not_awaited()
    case.repository.complete_upload.assert_not_awaited()
    case.session.commit.assert_not_awaited()
    case.session.rollback.assert_awaited_once()
    assert case.pending.status == "pending_upload"


@pytest.mark.asyncio
async def test_company_logo_completion_rechecks_current_write_authorization(completion_case):
    case = completion_case
    case.actor.permissions = ()
    case.loader.side_effect = CompanyProfilePermissionDeniedError("Permission revoked.")
    with pytest.raises(CompanyProfilePermissionDeniedError):
        await complete(case)
    case.loader.assert_awaited_once_with(actor=case.actor)
    assert_no_completion(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    "missing_actor", "inactive", "foreign_actor_tenant", "foreign_actor_user",
    "foreign_file_tenant", "foreign_file_owner", "foreign_entity", "foreign_file_id",
])
async def test_company_logo_completion_rejects_invalid_context(completion_case, invalid):
    case = completion_case
    overrides = {}
    if invalid == "missing_actor":
        overrides["actor"] = None
    elif invalid == "inactive":
        case.actor.status = "inactive"
    elif invalid.startswith("foreign_actor"):
        setattr(case.actor, "tenant_id" if invalid.endswith("tenant") else "user_id",
                UUID(int=99))
    else:
        field = {
            "foreign_file_tenant": "tenant_id", "foreign_file_owner": "owner_user_id",
            "foreign_entity": "entity_id", "foreign_file_id": "id",
        }[invalid]
        setattr(case.pending, field, UUID(int=99))
    with pytest.raises(CompanyProfilePermissionDeniedError):
        await complete(case, **overrides)
    case.loader.assert_not_awaited()
    assert_no_completion(case)


@pytest.mark.asyncio
async def test_company_logo_completion_requires_permission_even_after_context_load(completion_case):
    case = completion_case
    case.actor.permissions = ()
    with pytest.raises(CompanyProfilePermissionDeniedError):
        await complete(case)
    case.loader.assert_awaited_once()
    assert_no_completion(case)


@pytest.mark.asyncio
async def test_company_logo_completion_allows_authorized_tenant_manager(completion_case):
    case = completion_case
    order = []
    case.loader.side_effect = lambda **kwargs: order.append("authorize")
    case.storage.inspect_object.side_effect = lambda **kwargs: (
        order.append("inspect") or SimpleNamespace(
            exists=True, mime_type="image/png", size_bytes=1024, provider_metadata={},
        )
    )
    result = await complete(case)
    assert result.id == UUID(int=3)
    assert order == ["authorize", "inspect"]
    case.repository.complete_upload.assert_awaited_once()
    case.session.commit.assert_awaited_once()
    case.session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_company_logo_completion_allows_current_default_admin_grant(completion_case):
    case = completion_case
    case.actor.permissions = ()
    case.loader.return_value = SimpleNamespace(
        user_id=case.actor.user_id, tenant_id=case.actor.tenant_id,
        roles=("ADMIN",), grants=(SimpleNamespace(
            role="ADMIN", scope_type="tenant", scope_id=case.actor.tenant_id,
        ),),
    )
    await complete(case)
    case.repository.complete_upload.assert_awaited_once()
    case.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_company_logo_completion_rolls_back_on_authorization_lookup_failure(completion_case):
    case = completion_case
    case.loader.side_effect = RuntimeError("Authorization unavailable.")
    with pytest.raises(RuntimeError):
        await complete(case)
    assert_no_completion(case)


@pytest.mark.asyncio
async def test_regular_file_completion_keeps_existing_authorization_path(completion_case):
    case = completion_case
    case.pending.entity_type = "document"
    await complete(case, actor=None)
    case.loader.assert_not_awaited()
    case.repository.complete_upload.assert_awaited_once()
    case.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_company_logo_complete_endpoint_returns_sanitized_forbidden(monkeypatch):
    import httpx
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import get_api_request_rate_limit_service, get_file_service
    from services.api_identity import ApiActorContext

    actor = ApiActorContext(
        user_id=UUID(int=1), tenant_id=UUID(int=2), active_role="client",
        roles=("client",), language_code="uk", timezone="Europe/Kyiv", status="active",
    )
    service = SimpleNamespace(complete_upload=AsyncMock(
        side_effect=CompanyProfilePermissionDeniedError("private-test-storage-detail"),
    ))
    limiter = SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[get_file_service] = lambda: service
    app.dependency_overrides[get_api_request_rate_limit_service] = lambda: limiter
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(f"/api/v1/files/{UUID(int=3)}/complete")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert response.json()["error"]["message"] == "Organization file access is not available."
    assert "private-test" not in response.text
    service.complete_upload.assert_awaited_once_with(
        tenant_id=actor.tenant_id, owner_user_id=actor.user_id,
        file_id=UUID(int=3), actor=actor,
    )
