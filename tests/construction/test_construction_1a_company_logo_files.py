from construction_rate_limit_fixture import with_local_construction_rate_limit
from typing import get_args

from api.schemas import FileUploadRequest
from services.files import validate_file_upload


def test_platform_files_accepts_organization_logo_upload():
    allowed = get_args(
        FileUploadRequest.model_fields["object_type"].annotation
    )
    assert "organization" in allowed

    upload = validate_file_upload(
        object_type="organization",
        filename="logo.png",
        mime_type="image/png",
        size_bytes=1024,
    )
    assert upload.object_type == "organization"


import pytest
from types import SimpleNamespace
from uuid import uuid4

from services.files import FileService
from services.platform_company_profile_access import (
    CompanyProfilePermissionDeniedError,
    PLATFORM_ORGANIZATION_PROFILE_MANAGE,
)


@pytest.mark.asyncio
async def test_organization_upload_requires_profile_manage():
    tenant_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(),
    )
    service = FileService(
        repository=SimpleNamespace(),
        storage=object(),
        storage_provider_name="supabase",
    )

    with pytest.raises(CompanyProfilePermissionDeniedError):
        await service.create_upload_request(
            actor=actor,
            tenant_id=tenant_id,
            owner_user_id=actor.user_id,
            object_type="organization",
            entity_id=tenant_id,
            filename="logo.png",
            mime_type="image/png",
            size_bytes=1024,
        )


@pytest.mark.asyncio
async def test_organization_upload_rejects_foreign_entity_id():
    tenant_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,),
    )
    service = FileService(
        repository=SimpleNamespace(),
        storage=object(),
        storage_provider_name="supabase",
    )

    with pytest.raises(CompanyProfilePermissionDeniedError):
        await service.create_upload_request(
            actor=actor,
            tenant_id=tenant_id,
            owner_user_id=actor.user_id,
            object_type="organization",
            entity_id=uuid4(),
            filename="logo.png",
            mime_type="image/png",
            size_bytes=1024,
        )


from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_organization_upload_reserves_file_in_actor_tenant():
    tenant_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,),
    )
    file_id = uuid4()
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    repository = SimpleNamespace(
        session=session,
        create_pending_upload=AsyncMock(
            return_value=SimpleNamespace(
                id=file_id,
                status="pending_upload",
            )
        ),
    )
    storage = SimpleNamespace(
        create_signed_upload_url=AsyncMock(
            return_value="https://storage.example/signed-upload"
        ),
    )
    service = FileService(
        repository=repository,
        storage=storage,
        storage_provider_name="supabase",
    )

    result = await service.create_upload_request(
        actor=actor,
        tenant_id=tenant_id,
        owner_user_id=actor.user_id,
        object_type="organization",
        entity_id=tenant_id,
        filename="logo.png",
        mime_type="image/png",
        size_bytes=1024,
    )

    reserved = repository.create_pending_upload.call_args.kwargs
    assert reserved["tenant_id"] == tenant_id
    assert reserved["owner_user_id"] == actor.user_id
    assert reserved["entity_type"] == "organization"
    assert reserved["entity_id"] == tenant_id
    assert result.id == file_id
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_organization_download_lookup_requires_matching_tenant_entity():
    from sqlalchemy.dialects import postgresql
    from database.repositories.files import FileRepository

    tenant_id = uuid4()
    file_id = uuid4()
    expected = object()

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    result = await FileRepository(
        session
    ).get_organization_downloadable_file(
        tenant_id=tenant_id,
        file_id=file_id,
    )

    assert result is expected
    assert len(session.statements) == 1
    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    where_sql = sql.partition("WHERE ")[2]
    assert where_sql, sql
    assert f"file_storage_objects.tenant_id = '{tenant_id}'" in where_sql
    assert f"file_storage_objects.id = '{file_id}'" in where_sql
    assert "file_storage_objects.entity_type = 'organization'" in where_sql
    assert f"file_storage_objects.entity_id = '{tenant_id}'" in where_sql
    assert "file_storage_objects.status = 'ready'" in where_sql
    assert (
        "file_storage_objects.antivirus_status "
        "IN ('not_scanned', 'clean')"
    ) in where_sql
    assert "file_storage_objects.owner_user_id" not in where_sql


@pytest.mark.asyncio
async def test_organization_download_allows_other_active_tenant_member():
    from database.repositories.files import FileStorageObjectNotFoundError

    tenant_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(),
    )
    file_id = uuid4()
    storage_object = SimpleNamespace(
        id=file_id,
        tenant_id=tenant_id,
        owner_user_id=uuid4(),
        entity_type="organization",
        entity_id=tenant_id,
        storage_path=f"{tenant_id}/logo.png",
        mime_type="image/png",
        size_bytes=1024,
    )
    repository = SimpleNamespace(
        get_downloadable_file=AsyncMock(
            side_effect=FileStorageObjectNotFoundError(
                "Owner lookup did not find file."
            )
        ),
        get_organization_downloadable_file=AsyncMock(
            return_value=storage_object
        ),
    )
    storage = SimpleNamespace(
        create_signed_download_url=AsyncMock(
            return_value="https://storage.example/signed-download"
        ),
    )
    service = FileService(
        repository=repository,
        storage=storage,
        storage_provider_name="supabase",
    )

    result = await service.create_download_url(
        actor=actor,
        tenant_id=tenant_id,
        owner_user_id=actor.user_id,
        file_id=file_id,
    )

    repository.get_organization_downloadable_file.assert_awaited_once_with(
        tenant_id=tenant_id,
        file_id=file_id,
    )
    assert result.id == file_id
    assert result.download_url == (
        "https://storage.example/signed-download"
    )
    storage.create_signed_download_url.assert_awaited_once()


@pytest.mark.asyncio
async def test_organization_download_http_checks_member_context():
    from dataclasses import replace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_api_request_rate_limit_service,
        get_file_service,
    )
    from database.repositories.files import FileStorageObjectNotFoundError
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    file_id = uuid4()
    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )
    storage_object = SimpleNamespace(
        id=file_id,
        tenant_id=tenant_id,
        owner_user_id=uuid4(),
        entity_type="organization",
        entity_id=tenant_id,
        storage_path=f"{tenant_id}/logo.png",
        mime_type="image/png",
        size_bytes=1024,
    )
    repository = SimpleNamespace(
        get_downloadable_file=AsyncMock(
            side_effect=FileStorageObjectNotFoundError(
                "Not owned by requester."
            )
        ),
        get_organization_downloadable_file=AsyncMock(
            return_value=storage_object
        ),
    )
    storage = SimpleNamespace(
        create_signed_download_url=AsyncMock(
            return_value="https://storage.example/signed-download"
        ),
    )
    service = FileService(
        repository=repository,
        storage=storage,
        storage_provider_name="supabase",
    )

    class FakeRateLimit:
        async def ensure_authenticated_user_allowed(self, **kwargs):
            return None

    application = with_local_construction_rate_limit(create_app())
    actor_for_request = actor
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor_for_request
    application.dependency_overrides[
        get_file_service
    ] = lambda: service
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = lambda: FakeRateLimit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        path_url = f"/api/v1/files/{file_id}/download-url"
        allowed = await client.get(path_url)

        actor_for_request = replace(actor, status="inactive")
        inactive = await client.get(path_url)

        actor_for_request = replace(actor, tenant_id=uuid4())
        foreign = await client.get(path_url)

    assert allowed.status_code == 200
    assert allowed.json()["data"]["download_url"] == (
        "https://storage.example/signed-download"
    )
    assert inactive.status_code == 404
    assert foreign.status_code == 404
    assert storage.create_signed_download_url.await_count == 1
    assert repository.get_organization_downloadable_file.await_count == 2


def test_company_logo_file_validation_accepts_ready_private_image():
    from services.platform_company_profiles import validate_company_logo_file

    tenant_id = uuid4()
    file_id = uuid4()
    file = SimpleNamespace(
        id=file_id,
        tenant_id=tenant_id,
        entity_type="organization",
        entity_id=tenant_id,
        file_type="image",
        mime_type="image/png",
        size_bytes=1024,
        storage_path=f"{tenant_id}/logo.png",
        status="ready",
        antivirus_status="not_scanned",
        visibility_scope="private",
        public_url=None,
    )

    assert validate_company_logo_file(
        storage_object=file,
        tenant_id=tenant_id,
    ) == file_id


def test_company_logo_file_validation_rejects_wrong_or_public_file():
    from services.platform_company_profiles import (
        CompanyProfileValidationError,
        validate_company_logo_file,
    )

    tenant_id = uuid4()
    base = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "entity_type": "organization",
        "entity_id": tenant_id,
        "file_type": "image",
        "mime_type": "image/png",
        "size_bytes": 1024,
        "storage_path": f"{tenant_id}/logo.png",
        "status": "ready",
        "antivirus_status": "not_scanned",
        "visibility_scope": "private",
        "public_url": None,
    }
    invalid_cases = (
        {"tenant_id": uuid4()},
        {"entity_id": uuid4()},
        {"status": "pending_upload"},
        {"file_type": "pdf", "mime_type": "application/pdf"},
        {"visibility_scope": "public"},
        {"public_url": "https://public.example/logo.png"},
    )
    for change in invalid_cases:
        values = {**base, **change}
        with pytest.raises(CompanyProfileValidationError):
            validate_company_logo_file(
                storage_object=SimpleNamespace(**values),
                tenant_id=tenant_id,
            )


@pytest.mark.asyncio
async def test_company_profile_create_attaches_verified_logo_atomically():
    from services.platform_company_profiles import TenantCompanyProfileService

    tenant_id = uuid4()
    file_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,),
    )
    file = SimpleNamespace(
        id=file_id,
        tenant_id=tenant_id,
        entity_type="organization",
        entity_id=tenant_id,
        file_type="image",
        mime_type="image/png",
        size_bytes=1024,
        storage_path=f"{tenant_id}/logo.png",
        status="ready",
        antivirus_status="not_scanned",
        visibility_scope="private",
        public_url=None,
    )
    calls = []

    class FakeFiles:
        async def get_organization_downloadable_file(self, **kwargs):
            calls.append("verify_file")
            assert kwargs == {
                "tenant_id": tenant_id,
                "file_id": file_id,
            }
            return file

    class FakeProfiles:
        async def get_for_tenant(self, *, tenant_id):
            calls.append("find_profile")
            return None

        async def create_profile(self, **values):
            calls.append("create_profile")
            assert values["tenant_id"] == tenant_id
            assert values["logo_file_id"] == file_id
            return SimpleNamespace(
                id=uuid4(),
                tenant_id=tenant_id,
                logo_file_id=file_id,
            )

    class FakeEvents:
        async def create_event(self, **values):
            calls.append("audit")
            assert values["tenant_id"] == tenant_id
            assert values["user_id"] == actor.user_id
            assert "storage_path" not in str(values)
            assert "signed" not in str(values)

    class FakeSession:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    result = await TenantCompanyProfileService(
        session=FakeSession(),
        repository=FakeProfiles(),
        event_repository=FakeEvents(),
        file_repository=FakeFiles(),
    ).create_profile(
        actor=actor,
        legal_name="Example Company",
        email=None,
        website=None,
        phone=None,
        logo_file_id=file_id,
    )

    assert result.logo_file_id == file_id
    assert calls == [
        "find_profile",
        "verify_file",
        "create_profile",
        "audit",
        "commit",
    ]


@pytest.mark.asyncio
async def test_company_profile_update_replaces_logo_with_version_and_audit():
    from services.platform_company_profiles import TenantCompanyProfileService

    tenant_id = uuid4()
    old_file_id = uuid4()
    new_file_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,),
    )
    profile = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        row_version=3,
        legal_name="Example Company",
        logo_file_id=old_file_id,
    )
    new_file = SimpleNamespace(
        id=new_file_id,
        tenant_id=tenant_id,
        entity_type="organization",
        entity_id=tenant_id,
        file_type="image",
        mime_type="image/png",
        size_bytes=1024,
        storage_path=f"{tenant_id}/new-logo.png",
        status="ready",
        antivirus_status="clean",
        visibility_scope="private",
        public_url=None,
    )
    calls = []

    class FakeFiles:
        async def get_organization_downloadable_file(self, **kwargs):
            calls.append("verify_new_file")
            assert kwargs == {
                "tenant_id": tenant_id,
                "file_id": new_file_id,
            }
            return new_file

    class FakeProfiles:
        async def get_for_tenant_for_update(self, *, tenant_id):
            calls.append("lock_profile")
            assert tenant_id == actor.tenant_id
            return profile

        async def update_profile(self, *, profile, values):
            calls.append("update_profile")
            assert values == {"logo_file_id": new_file_id}
            profile.logo_file_id = values["logo_file_id"]
            return profile

    class FakeEvents:
        async def create_event(self, **values):
            calls.append("audit")
            assert values["event_type"] == "platform_company_profile_updated"
            assert values["payload"] == {
                "operation": "update",
                "fields": ["logo_file_id"],
            }
            assert "storage_path" not in str(values)

    class FakeSession:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    result = await TenantCompanyProfileService(
        session=FakeSession(),
        repository=FakeProfiles(),
        event_repository=FakeEvents(),
        file_repository=FakeFiles(),
    ).update_profile(
        actor=actor,
        expected_row_version=3,
        changes={"logo_file_id": new_file_id},
        trace_id="logo-replacement-test",
    )

    assert result.logo_file_id == new_file_id
    assert calls == [
        "lock_profile",
        "verify_new_file",
        "update_profile",
        "audit",
        "commit",
    ]


@pytest.mark.asyncio
async def test_company_logo_update_rejects_foreign_file_before_mutation():
    from services.platform_company_profiles import (
        CompanyProfileValidationError,
        TenantCompanyProfileService,
    )

    tenant_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,),
    )
    profile = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        row_version=1,
        logo_file_id=None,
    )
    profiles = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=profile),
        update_profile=AsyncMock(),
    )
    files = SimpleNamespace(
        get_organization_downloadable_file=AsyncMock(
            return_value=SimpleNamespace(tenant_id=uuid4())
        ),
    )
    events = SimpleNamespace(create_event=AsyncMock())
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    with pytest.raises(CompanyProfileValidationError):
        await TenantCompanyProfileService(
            session=session,
            repository=profiles,
            event_repository=events,
            file_repository=files,
        ).update_profile(
            actor=actor,
            expected_row_version=1,
            changes={"logo_file_id": uuid4()},
        )

    profiles.update_profile.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_company_logo_update_rolls_back_on_audit_failure():
    from services.platform_company_profiles import (
        TenantCompanyProfileOperationError,
        TenantCompanyProfileService,
    )

    tenant_id = uuid4()
    file_id = uuid4()
    actor = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,),
    )
    profile = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        row_version=1,
        logo_file_id=None,
    )
    file = SimpleNamespace(
        id=file_id,
        tenant_id=tenant_id,
        entity_type="organization",
        entity_id=tenant_id,
        file_type="image",
        mime_type="image/png",
        size_bytes=1024,
        storage_path=f"{tenant_id}/logo.png",
        status="ready",
        antivirus_status="clean",
        visibility_scope="private",
        public_url=None,
    )
    profiles = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=profile),
        update_profile=AsyncMock(return_value=profile),
    )
    files = SimpleNamespace(
        get_organization_downloadable_file=AsyncMock(return_value=file),
    )
    events = SimpleNamespace(
        create_event=AsyncMock(side_effect=RuntimeError("audit unavailable")),
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    with pytest.raises(TenantCompanyProfileOperationError):
        await TenantCompanyProfileService(
            session=session,
            repository=profiles,
            event_repository=events,
            file_repository=files,
        ).update_profile(
            actor=actor,
            expected_row_version=1,
            changes={"logo_file_id": file_id},
        )

    profiles.update_profile.assert_awaited_once()
    events.create_event.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
