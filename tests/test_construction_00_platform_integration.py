import pytest
from api.app import API_PREFIX, create_app


def test_con_p002_reserves_construction_api_namespace():
    from api.routes import construction_router
    from api.routes.construction import (
        router,
    )

    assert construction_router is router
    assert router.prefix == "/construction"
    assert (
        f"{API_PREFIX}{router.prefix}"
        == "/api/v1/construction"
    )
    assert (
        "construction_router"
        in create_app.__code__.co_names
    )



def test_con_p003_reuses_platform_identity_organization_scope_and_auth():
    import database.models as models

    from api.auth import get_current_actor
    from api.routes.construction import router
    from services.api_identity import (
        ApiActorContext,
    )

    assert models.User.__tablename__ == "users"
    assert models.Tenant.__tablename__ == "tenants"

    assert {
        "user_id",
        "tenant_id",
    } <= set(
        ApiActorContext.__dataclass_fields__
    )

    assert not hasattr(
        models,
        "ConstructionUser",
    )
    assert not hasattr(
        models,
        "ConstructionOrganization",
    )

    assert get_current_actor in [
        dependency.dependency
        for dependency in router.dependencies
    ]



def test_con_p004_uses_construction_table_prefix():
    from database.construction import (
        CONSTRUCTION_TABLE_PREFIX,
        construction_table_name,
    )

    assert (
        CONSTRUCTION_TABLE_PREFIX
        == "construction_"
    )
    assert (
        construction_table_name("projects")
        == "construction_projects"
    )
    assert (
        construction_table_name(
            "measurement_sessions"
        )
        == "construction_measurement_sessions"
    )



def test_con_p005_server_tenant_scope_is_fail_closed():
    from uuid import uuid4

    import pytest

    from database.construction import (
        CONSTRUCTION_RLS_REQUIRED,
        CONSTRUCTION_TENANT_COLUMN,
    )
    from services.construction_access import (
        ConstructionTenantScopeError,
        require_construction_tenant_scope,
    )

    actor_tenant_id = uuid4()
    foreign_tenant_id = uuid4()

    assert CONSTRUCTION_RLS_REQUIRED is True
    assert (
        CONSTRUCTION_TENANT_COLUMN
        == "tenant_id"
    )

    assert (
        require_construction_tenant_scope(
            actor_tenant_id=actor_tenant_id,
            resource_tenant_id=actor_tenant_id,
        )
        == actor_tenant_id
    )

    with pytest.raises(
        ConstructionTenantScopeError
    ):
        require_construction_tenant_scope(
            actor_tenant_id=actor_tenant_id,
            resource_tenant_id=(
                foreign_tenant_id
            ),
        )

    with pytest.raises(
        ConstructionTenantScopeError
    ):
        require_construction_tenant_scope(
            actor_tenant_id=None,
            resource_tenant_id=(
                actor_tenant_id
            ),
        )



def test_con_p006_uses_platform_storage_paths_and_signed_access():
    from uuid import uuid4

    from services.construction_storage import (
        CONSTRUCTION_SIGNED_URL_TTL_SECONDS,
        construction_branding_storage_prefix,
        construction_project_storage_prefix,
    )
    from services.file_storage import (
        FILE_SIGNED_URL_TTL_SECONDS,
    )

    organization_id = uuid4()
    project_id = uuid4()

    root = (
        f"organization/{organization_id}"
        "/construction"
    )

    assert (
        construction_project_storage_prefix(
            organization_id=organization_id,
            project_id=project_id,
            section="measurements",
        )
        == (
            f"{root}/projects/{project_id}"
            "/measurements/"
        )
    )
    assert (
        construction_project_storage_prefix(
            organization_id=organization_id,
            project_id=project_id,
            section="offers",
        )
        == (
            f"{root}/projects/{project_id}"
            "/offers/"
        )
    )
    assert (
        construction_branding_storage_prefix(
            organization_id=organization_id,
        )
        == f"{root}/branding/"
    )

    assert (
        CONSTRUCTION_SIGNED_URL_TTL_SECONDS
        == FILE_SIGNED_URL_TTL_SECONDS
        == 15 * 60
    )



def test_con_p007_defines_background_job_contract():
    from dataclasses import fields

    from services.construction_jobs import (
        ConstructionBackgroundJob,
        ConstructionJobStatus,
        ConstructionJobType,
    )

    assert {
        status.value
        for status in ConstructionJobStatus
    } == {
        "QUEUED",
        "RUNNING",
        "RETRY",
        "READY",
        "FAILED",
        "CANCELLED",
    }

    assert {
        job_type.value
        for job_type in ConstructionJobType
    } == {
        "EXPORT_PDF",
        "EXPORT_XLSX",
        "BULK_IMPORT",
        "SUPPLIER_SYNC",
        "THUMBNAIL_GENERATION",
        "VOICE_TRANSCRIPTION",
        "RETAIL_PRICE_SEARCH",
    }

    assert {
        field.name
        for field in fields(
            ConstructionBackgroundJob
        )
    } >= {
        "id",
        "tenant_id",
        "user_id",
        "resource_type",
        "resource_id",
        "job_type",
        "status",
        "idempotency_key",
        "attempts",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "progress",
        "error_code",
        "result_reference",
    }



def test_con_p008_requires_suite_module_and_platform_permission_guard():
    import inspect

    from api.auth import require_permission
    from api.routes.construction import router

    dependency_scopes = [
        inspect.getclosurevars(
            dependency.dependency
        ).nonlocals
        for dependency in router.dependencies
    ]

    assert any(
        scope.get("normalized_suite")
        == "construction"
        for scope in dependency_scopes
    )
    assert any(
        scope.get("normalized_module")
        == "construction.core"
        for scope in dependency_scopes
    )

    permission_guard = require_permission(
        "construction.projects.read"
    )
    permission_scope = inspect.getclosurevars(
        permission_guard
    ).nonlocals

    assert (
        permission_scope[
            "normalized_permission"
        ]
        == "construction.projects.read"
    )



def test_con_p008_rejects_routes_without_explicit_construction_permission():
    import pytest
    from fastapi import Depends

    from api.routes.construction import (
        ConstructionAPIRouter,
        require_construction_permission,
    )

    test_router = ConstructionAPIRouter()

    async def endpoint():
        return None

    with pytest.raises(
        ValueError,
        match="permission",
    ):
        test_router.add_api_route(
            "/unsafe",
            endpoint,
            methods=["GET"],
        )

    permission_guard = (
        require_construction_permission(
            "construction.projects.read"
        )
    )

    test_router.add_api_route(
        "/safe",
        endpoint,
        methods=["GET"],
        dependencies=[
            Depends(permission_guard),
        ],
    )

    assert (
        permission_guard
        .construction_permission_code
        == "construction.projects.read"
    )
    assert len(test_router.routes) == 1



async def test_con_p006_signed_access_is_scoped_and_uses_platform_provider():
    from uuid import uuid4

    import pytest

    from services.construction_access import (
        ConstructionTenantScopeError,
    )
    from services.construction_storage import (
        CONSTRUCTION_SIGNED_URL_TTL_SECONDS,
        ConstructionStorageAccessService,
        ConstructionStoragePathError,
    )

    tenant_id = uuid4()
    foreign_tenant_id = uuid4()
    project_id = uuid4()
    calls = []

    class FakeStorageProvider:
        async def create_signed_download_url(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (
                "https://storage.example/"
                "signed-download"
            )

    service = ConstructionStorageAccessService(
        FakeStorageProvider()
    )
    storage_path = (
        f"organization/{tenant_id}"
        f"/construction/projects/{project_id}"
        "/measurements/photo.jpg"
    )

    result = await service.create_signed_download_url(
        actor_tenant_id=tenant_id,
        resource_tenant_id=tenant_id,
        storage_path=storage_path,
    )

    assert result == (
        "https://storage.example/"
        "signed-download"
    )
    assert calls == [
        {
            "storage_path": storage_path,
            "expires_in": (
                CONSTRUCTION_SIGNED_URL_TTL_SECONDS
            ),
        }
    ]

    with pytest.raises(
        ConstructionTenantScopeError
    ):
        await service.create_signed_download_url(
            actor_tenant_id=foreign_tenant_id,
            resource_tenant_id=tenant_id,
            storage_path=storage_path,
        )

    with pytest.raises(
        ConstructionStoragePathError
    ):
        await service.create_signed_download_url(
            actor_tenant_id=tenant_id,
            resource_tenant_id=tenant_id,
            storage_path="public/photo.jpg",
        )

    assert len(calls) == 1



async def test_con_p007_dispatches_heavy_work_through_queue_contract():
    from datetime import UTC, datetime
    from uuid import uuid4

    from services.construction_jobs import (
        ConstructionBackgroundJob,
        ConstructionBackgroundJobDispatcher,
        ConstructionJobQueue,
        ConstructionJobStatus,
        ConstructionJobType,
    )

    job = ConstructionBackgroundJob(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        resource_type="estimate",
        resource_id=uuid4(),
        job_type=(
            ConstructionJobType.EXPORT_PDF
        ),
        status=ConstructionJobStatus.QUEUED,
        idempotency_key="export-pdf-001",
        attempts=0,
        available_at=datetime(
            2026,
            9,
            15,
            12,
            0,
            tzinfo=UTC,
        ),
        lease_owner=None,
        lease_expires_at=None,
        progress=0,
        error_code=None,
        result_reference=None,
    )
    calls = []

    class FakeQueue:
        async def enqueue(self, *, job):
            calls.append(job)
            return job.id

    queue: ConstructionJobQueue = FakeQueue()
    dispatcher = (
        ConstructionBackgroundJobDispatcher(
            queue=queue,
        )
    )

    result = await dispatcher.enqueue(
        job=job,
    )

    assert result == job.id
    assert calls == [job]



def test_con_p005_matches_platform_rls_and_rejects_unscoped_tables():
    from uuid import UUID

    import pytest
    from sqlalchemy import (
        Column,
        ForeignKey,
        MetaData,
        Table,
    )
    from sqlalchemy.types import Uuid

    from database.construction import (
        CONSTRUCTION_RLS_CONTRACT,
        ConstructionTableContractError,
        validate_construction_table_contract,
    )

    assert (
        CONSTRUCTION_RLS_CONTRACT.enabled
        is True
    )
    assert (
        CONSTRUCTION_RLS_CONTRACT.forced
        is False
    )
    assert (
        CONSTRUCTION_RLS_CONTRACT.policies
        == ("construction_tenant_isolation",)
    )
    assert (
        CONSTRUCTION_RLS_CONTRACT.client_grants
        == ()
    )

    metadata = MetaData()

    scoped_table = Table(
        "construction_projects",
        metadata,
        Column(
            "id",
            Uuid(as_uuid=True),
            primary_key=True,
        ),
        Column(
            "tenant_id",
            Uuid(as_uuid=True),
            ForeignKey("tenants.id"),
            nullable=False,
        ),
    )

    assert (
        validate_construction_table_contract(
            scoped_table
        )
        is scoped_table
    )

    unscoped_table = Table(
        "construction_unscoped",
        metadata,
        Column(
            "id",
            Uuid(as_uuid=True),
            primary_key=True,
        ),
    )

    with pytest.raises(
        ConstructionTableContractError
    ):
        validate_construction_table_contract(
            unscoped_table
        )



async def test_con_p008_returns_exact_module_disabled_code():
    import inspect
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from api.errors import ApiHttpError
    from api.routes.construction import router
    from services.module_access import (
        ModuleDisabledError,
        SuiteDisabledError,
    )

    dependencies = [
        dependency.dependency
        for dependency in router.dependencies
    ]

    suite_dependency = next(
        dependency
        for dependency in dependencies
        if inspect.getclosurevars(
            dependency
        ).nonlocals.get(
            "normalized_suite"
        ) == "construction"
    )
    module_dependency = next(
        dependency
        for dependency in dependencies
        if inspect.getclosurevars(
            dependency
        ).nonlocals.get(
            "normalized_module"
        ) == "construction.core"
    )

    actor = SimpleNamespace(
        tenant_id=uuid4()
    )

    class DisabledSuiteService:
        async def require_enabled(
            self,
            **kwargs,
        ):
            raise SuiteDisabledError(
                "Private details."
            )

    class DisabledModuleService:
        async def require_enabled(
            self,
            **kwargs,
        ):
            raise ModuleDisabledError(
                "Private details."
            )

    with pytest.raises(
        ApiHttpError
    ) as suite_error:
        await suite_dependency(
            actor,
            DisabledSuiteService(),
        )

    with pytest.raises(
        ApiHttpError
    ) as module_error:
        await module_dependency(
            actor,
            DisabledModuleService(),
        )

    assert (
        suite_error.value.code
        == "MODULE_DISABLED"
    )
    assert (
        module_error.value.code
        == "MODULE_DISABLED"
    )



async def test_con_p006_signed_upload_uses_scoped_platform_provider():
    from uuid import uuid4

    from services.construction_storage import (
        CONSTRUCTION_SIGNED_UPLOAD_URL_TTL_SECONDS,
        ConstructionStorageAccessService,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    calls = []

    class FakeStorageProvider:
        async def create_signed_upload_url(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (
                "https://storage.example/"
                "signed-upload"
            )

    service = ConstructionStorageAccessService(
        FakeStorageProvider()
    )
    storage_path = (
        f"organization/{tenant_id}"
        f"/construction/projects/{project_id}"
        "/measurements/photo.jpg"
    )

    result = await service.create_signed_upload_url(
        actor_tenant_id=tenant_id,
        resource_tenant_id=tenant_id,
        storage_path=storage_path,
        mime_type="image/jpeg",
    )

    assert result == (
        "https://storage.example/"
        "signed-upload"
    )
    assert calls == [
        {
            "storage_path": storage_path,
            "mime_type": "image/jpeg",
            "expires_in": (
                CONSTRUCTION_SIGNED_UPLOAD_URL_TTL_SECONDS
            ),
        }
    ]



def test_con_p006_factory_reuses_configured_platform_supabase_provider(
    monkeypatch,
):
    from services.construction_storage import (
        build_construction_storage_access_service,
    )
    from services.portfolio_storage import (
        SupabaseFileStorage,
    )

    monkeypatch.setenv(
        "SUPABASE_URL",
        "https://project.supabase.co",
    )
    monkeypatch.setenv(
        "SUPABASE_SERVICE_ROLE_KEY",
        "test-service-role-key",
    )
    monkeypatch.setenv(
        "SUPABASE_STORAGE_BUCKET",
        "platform-files",
    )

    service = (
        build_construction_storage_access_service()
    )

    assert isinstance(
        service.storage,
        SupabaseFileStorage,
    )
    assert service.storage.bucket == "platform-files"



def test_con_p005_builds_safe_supabase_table_security_sql():
    import pytest

    from database.construction import (
        ConstructionTableContractError,
        build_construction_table_security_sql,
    )

    assert build_construction_table_security_sql(
        "construction_projects"
    ) == (
        "ALTER TABLE public.construction_projects "
        "ENABLE ROW LEVEL SECURITY;",
        "REVOKE ALL ON TABLE "
        "public.construction_projects "
        "FROM anon, authenticated;",
        "CREATE POLICY "
        "construction_tenant_isolation "
        "ON public.construction_projects "
        "USING (tenant_id = "
        "current_setting("
        "'app.current_tenant_id'"
        ")::uuid) "
        "WITH CHECK (tenant_id = "
        "current_setting("
        "'app.current_tenant_id'"
        ")::uuid);",
    )

    for invalid_name in (
        "projects",
        "construction_",
        "construction-projects",
        "construction_projects; DROP TABLE users",
    ):
        with pytest.raises(
            ConstructionTableContractError
        ):
            build_construction_table_security_sql(
                invalid_name
            )



async def test_con_p003_accepts_selected_platform_organization_with_active_membership():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_identity import (
        ApiIdentityService,
    )

    user_id = uuid4()
    primary_tenant_id = uuid4()
    selected_tenant_id = uuid4()
    calls = []

    user = SimpleNamespace(
        id=user_id,
        tenant_id=primary_tenant_id,
        active_role="client",
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeUserService:
        async def get_user_by_id(
            self,
            value,
        ):
            assert value == user_id
            return user

    class FakeUserRepository:
        async def has_active_tenant_membership(
            self,
            *,
            user_id,
            tenant_id,
        ):
            calls.append(
                (
                    "membership",
                    user_id,
                    tenant_id,
                )
            )
            return True

        async def list_active_roles(
            self,
            value,
            *,
            tenant_id,
        ):
            assert value == user_id
            assert tenant_id == selected_tenant_id
            return ["client"]

        async def list_active_permissions(
            self,
            *,
            roles,
        ):
            assert roles == ("client",)
            return []

        async def list_active_role_scopes(
            self,
            *,
            user_id,
            tenant_id,
            roles,
        ):
            assert tenant_id == selected_tenant_id
            return []

    actor = await ApiIdentityService(
        session=None,
        user_service=FakeUserService(),
        user_repository=FakeUserRepository(),
    ).require_actor(
        user_id=user_id,
        tenant_id=selected_tenant_id,
    )

    assert actor.user_id == user_id
    assert actor.tenant_id == selected_tenant_id
    assert actor.roles == ("client",)
    assert calls == [
        (
            "membership",
            user_id,
            selected_tenant_id,
        )
    ]



async def test_con_p008_enforces_all_guards_through_http():
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx
    from fastapi import Depends

    from api.app import API_PREFIX, create_app
    from api.auth import get_current_actor
    from api.errors import ApiHttpError
    from api.routes.construction import (
        ConstructionAPIRouter,
        get_construction_scoped_session,
        require_construction_permission,
        router as construction_router,
    )

    permission = (
        "construction.projects.read"
    )
    permission_guard = (
        require_construction_permission(
            permission
        )
    )

    test_router = ConstructionAPIRouter(
        prefix="/construction",
        dependencies=(
            construction_router.dependencies
        ),
    )

    @test_router.get(
        "/guard-probe",
        dependencies=[
            Depends(permission_guard),
        ],
    )
    async def guard_probe():
        return {"ok": True}

    application = create_app()
    application.include_router(
        test_router,
        prefix=API_PREFIX,
    )

    transport = httpx.ASGITransport(
        app=application,
        raise_app_exceptions=False,
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/v1/construction/guard-probe"
        )
        assert response.status_code == 401

        actor = SimpleNamespace(
            user_id=uuid4(),
            tenant_id=uuid4(),
            permissions=(permission,),
        )

        application.dependency_overrides[
            get_current_actor
        ] = lambda: actor

        application.dependency_overrides[
            get_construction_scoped_session
        ] = lambda: object()

        suite_guard = (
            construction_router
            .dependencies[2]
            .dependency
        )
        module_guard = (
            construction_router
            .dependencies[3]
            .dependency
        )

        application.dependency_overrides[
            suite_guard
        ] = lambda: actor
        application.dependency_overrides[
            module_guard
        ] = lambda: actor

        response = await client.get(
            "/api/v1/construction/guard-probe"
        )
        assert response.status_code == 200

        async def disabled_suite():
            raise ApiHttpError(
                status_code=403,
                code="MODULE_DISABLED",
                message=(
                    "Required module is "
                    "not available."
                ),
            )

        application.dependency_overrides[
            suite_guard
        ] = disabled_suite

        response = await client.get(
            "/api/v1/construction/guard-probe"
        )
        assert response.status_code == 403
        assert (
            response.json()["error"]["code"]
            == "MODULE_DISABLED"
        )

        application.dependency_overrides[
            suite_guard
        ] = lambda: actor

        actor.permissions = ()

        response = await client.get(
            "/api/v1/construction/guard-probe"
        )
        assert response.status_code == 403

async def test_con_p003_switches_role_in_selected_secondary_organization():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.api_identity import (
        ApiActorContext,
        ApiIdentityService,
    )

    user_id = uuid4()
    primary_tenant_id = uuid4()
    selected_tenant_id = uuid4()
    calls = []

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeUserRepository:
        async def set_active_role(
            self,
            value,
            role,
        ):
            calls.append(
                (
                    "set_active_role",
                    value,
                    role,
                )
            )
            return SimpleNamespace(
                id=user_id,
                tenant_id=primary_tenant_id,
                active_role=role,
            )

    class FakeEventRepository:
        async def create_event(self, **kwargs):
            calls.append(("event", kwargs))

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=selected_tenant_id,
        active_role="client",
        roles=("client", "specialist"),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    service = ApiIdentityService(
        FakeSession(),
        user_service=object(),
        user_repository=FakeUserRepository(),
        event_repository=FakeEventRepository(),
        translation_service=object(),
    )

    result = await service.switch_active_role(
        actor=actor,
        role="specialist",
    )

    assert result.user_id == user_id
    assert result.tenant_id == selected_tenant_id
    assert result.active_role == "specialist"
    assert calls[-1] == ("commit", {})
    assert not any(
        call[0] == "rollback"
        for call in calls
    )


async def test_con_p005_sets_verified_tenant_context_for_current_transaction():
    from uuid import uuid4

    from database.construction import (
        set_construction_tenant_context,
    )

    tenant_id = uuid4()
    calls = []

    class FakeSession:
        async def execute(
            self,
            statement,
            parameters,
        ):
            calls.append(
                (
                    str(statement),
                    parameters,
                )
            )

    session = FakeSession()

    await set_construction_tenant_context(
        session,
        tenant_id=tenant_id,
    )

    assert calls == [
        (
            "SELECT set_config("
            "'app.current_tenant_id', "
            ":tenant_id, true"
            ")",
            {
                "tenant_id": str(tenant_id),
            },
        )
    ]



def test_con_p005_requires_dedicated_construction_database_identity(
    monkeypatch,
):
    import pytest

    from database.construction_session import (
        ConstructionDatabaseConfigurationError,
        get_construction_database_url,
    )

    platform_url = (
        "postgresql+asyncpg://postgres:"
        "secret@db.example/platform"
    )
    construction_url = (
        "postgresql+asyncpg://construction_api:"
        "secret@db.example/platform"
    )

    monkeypatch.setenv(
        "DATABASE_URL",
        platform_url,
    )
    monkeypatch.setenv(
        "CONSTRUCTION_DATABASE_URL",
        construction_url,
    )

    assert (
        get_construction_database_url()
        == construction_url
    )

    monkeypatch.setenv(
        "CONSTRUCTION_DATABASE_URL",
        platform_url,
    )

    with pytest.raises(
        ConstructionDatabaseConfigurationError
    ):
        get_construction_database_url()

    monkeypatch.delenv(
        "CONSTRUCTION_DATABASE_URL"
    )

    with pytest.raises(
        ConstructionDatabaseConfigurationError
    ):
        get_construction_database_url()



def test_con_p005_builds_session_factory_from_dedicated_database_identity(
    monkeypatch,
):
    import database.construction_session as session_module

    platform_url = (
        "postgresql+asyncpg://postgres:secret@"
        "db.example/platform"
    )
    construction_url = (
        "postgresql+asyncpg://construction_api:secret@"
        "db.example/platform"
    )
    engine = object()
    session_factory = object()
    calls = []

    def fake_create_async_engine(url, **kwargs):
        calls.append(
            (
                "engine",
                url,
                kwargs,
            )
        )
        return engine

    def fake_async_sessionmaker(
        bound_engine,
        **kwargs,
    ):
        calls.append(
            (
                "session_factory",
                bound_engine,
                kwargs,
            )
        )
        return session_factory

    monkeypatch.setenv(
        "DATABASE_URL",
        platform_url,
    )
    monkeypatch.setenv(
        "CONSTRUCTION_DATABASE_URL",
        construction_url,
    )
    monkeypatch.setattr(
        session_module,
        "create_async_engine",
        fake_create_async_engine,
        raising=False,
    )
    monkeypatch.setattr(
        session_module,
        "async_sessionmaker",
        fake_async_sessionmaker,
        raising=False,
    )

    result = (
        session_module
        .build_construction_session_factory()
    )

    assert result is session_factory
    assert calls[0] == (
        "engine",
        construction_url,
        {
            "pool_pre_ping": True,
        },
    )
    assert calls[1][0] == "session_factory"
    assert calls[1][1] is engine
    assert calls[1][2]["expire_on_commit"] is False



@pytest.mark.asyncio
async def test_con_p005_opens_scoped_construction_transaction():
    from uuid import uuid4

    from database.construction_session import (
        construction_transaction,
    )

    tenant_id = uuid4()
    calls = []

    class FakeTransaction:
        async def __aenter__(self):
            calls.append(("transaction_enter",))
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            calls.append(
                (
                    "transaction_exit",
                    exc_type,
                )
            )

    class FakeSession:
        async def __aenter__(self):
            calls.append(("session_enter",))
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            traceback,
        ):
            calls.append(
                (
                    "session_exit",
                    exc_type,
                )
            )

        def begin(self):
            calls.append(("begin",))
            return FakeTransaction()

        async def execute(
            self,
            statement,
            parameters,
        ):
            calls.append(
                (
                    "execute",
                    str(statement),
                    parameters,
                )
            )

    session = FakeSession()

    class FakeIdentityGuard:
        async def ensure_verified(
            self,
            active_session,
        ):
            calls.append(
                (
                    "identity_verified",
                    active_session,
                )
            )
            return "construction_api"

    identity_guard = FakeIdentityGuard()

    def session_factory():
        calls.append(("factory",))
        return session

    async with construction_transaction(
        tenant_id=tenant_id,
        session_factory=session_factory,
        identity_guard=identity_guard,
    ) as scoped_session:
        assert scoped_session is session
        calls.append(("body",))

    assert calls[0:4] == [
        ("factory",),
        ("session_enter",),
        ("begin",),
        ("transaction_enter",),
    ]

    assert calls[4] == (
        "identity_verified",
        session,
    )

    execute_call = next(
        call
        for call in calls
        if call[0] == "execute"
    )
    assert "set_config" in execute_call[1]
    assert execute_call[2] == {
        "tenant_id": str(tenant_id),
    }

    assert calls[-3:] == [
        ("body",),
        ("transaction_exit", None),
        ("session_exit", None),
    ]



@pytest.mark.asyncio
async def test_con_p005_http_dependency_uses_verified_actor_tenant(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from uuid import uuid4

    import api.routes.construction as route_module
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    session = object()
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    @asynccontextmanager
    async def fake_construction_transaction(
        *,
        tenant_id,
    ):
        calls.append(("enter", tenant_id))
        try:
            yield session
        finally:
            calls.append(("exit", tenant_id))

    monkeypatch.setattr(
        route_module,
        "construction_transaction",
        fake_construction_transaction,
        raising=False,
    )

    dependency = (
        route_module
        .get_construction_scoped_session(
            actor=actor,
        )
    )

    scoped_session = await anext(dependency)
    assert scoped_session is session
    assert calls == [
        ("enter", tenant_id),
    ]

    await dependency.aclose()

    assert calls == [
        ("enter", tenant_id),
        ("exit", tenant_id),
    ]



def test_con_p005_all_construction_routes_use_scoped_transaction():
    from api.routes.construction import (
        get_construction_scoped_session,
        router,
    )

    dependencies = [
        dependency.dependency
        for dependency in router.dependencies
    ]

    assert (
        get_construction_scoped_session
        in dependencies
    )



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role_row", "allowed"),
    [
        (
            {
                "role_name": "construction_api",
                "bypasses_rls": False,
            },
            True,
        ),
        (
            {
                "role_name": "construction_api",
                "bypasses_rls": True,
            },
            False,
        ),
        (
            None,
            False,
        ),
    ],
)
async def test_con_p005_verifies_active_database_role_without_bypassrls(
    role_row,
    allowed,
):
    from database.construction_session import (
        ConstructionDatabaseConfigurationError,
        verify_construction_database_identity,
    )

    class FakeMappings:
        def one_or_none(self):
            return role_row

    class FakeResult:
        def mappings(self):
            return FakeMappings()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(str(statement))
            return FakeResult()

    session = FakeSession()

    if allowed:
        role_name = (
            await verify_construction_database_identity(
                session
            )
        )
        assert role_name == "construction_api"
    else:
        with pytest.raises(
            ConstructionDatabaseConfigurationError
        ):
            await verify_construction_database_identity(
                session
            )

    assert len(session.statements) == 1
    assert "current_user" in session.statements[0]
    assert "rolbypassrls" in session.statements[0]



@pytest.mark.asyncio
async def test_con_p005_database_identity_guard_caches_only_successful_verification(
    monkeypatch,
):
    import database.construction_session as session_module

    session = object()
    calls = []
    outcomes = [
        session_module.ConstructionDatabaseConfigurationError(
            "Unsafe role."
        ),
        "construction_api",
    ]

    async def fake_verify(active_session):
        calls.append(active_session)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(
        session_module,
        "verify_construction_database_identity",
        fake_verify,
    )

    guard = (
        session_module
        .ConstructionDatabaseIdentityGuard()
    )

    with pytest.raises(
        session_module
        .ConstructionDatabaseConfigurationError
    ):
        await guard.ensure_verified(session)

    assert calls == [session]

    assert (
        await guard.ensure_verified(session)
        == "construction_api"
    )
    assert calls == [
        session,
        session,
    ]

    assert (
        await guard.ensure_verified(session)
        == "construction_api"
    )
    assert calls == [
        session,
        session,
    ]



def test_con_p005_documents_dedicated_database_role_contract():
    from pathlib import Path

    environment_example = Path(
        ".env.example"
    ).read_text(encoding="utf-8-sig")
    server_checklist = Path(
        "docs/server_env_checklist.md"
    ).read_text(encoding="utf-8-sig")

    assert (
        "CONSTRUCTION_DATABASE_URL="
        in environment_example
    )
    assert (
        environment_example.count(
            "CONSTRUCTION_DATABASE_URL="
        )
        == 1
    )

    assert "CONSTRUCTION_DATABASE_URL" in server_checklist
    assert (
        "same Supabase PostgreSQL database"
        in server_checklist
    )
    assert "BYPASSRLS" in server_checklist
    assert "app.current_tenant_id" in server_checklist
    assert "tenant_id" in server_checklist



def test_con_p005_reuses_process_construction_session_factory(
    monkeypatch,
):
    import database.construction_session as session_module

    session_factory = object()
    calls = []

    def fake_build():
        calls.append("build")
        return session_factory

    monkeypatch.setattr(
        session_module,
        "build_construction_session_factory",
        fake_build,
    )

    shared_factory = (
        session_module
        .get_construction_session_factory
    )
    shared_factory.cache_clear()

    try:
        first = shared_factory()
        second = shared_factory()
    finally:
        shared_factory.cache_clear()

    assert first is session_factory
    assert second is session_factory
    assert calls == ["build"]



@pytest.mark.parametrize(
    "construction_url",
    [
        (
            "postgresql+asyncpg://construction_api:secret@"
            "db.example/other_database"
        ),
        (
            "postgresql+asyncpg://construction_api:secret@"
            "other-db.example/platform"
        ),
        (
            "postgresql+asyncpg://construction_api:secret@"
            "db.example:6543/platform"
        ),
    ],
)
def test_con_p005_rejects_construction_url_for_different_database(
    monkeypatch,
    construction_url,
):
    from database.construction_session import (
        ConstructionDatabaseConfigurationError,
        get_construction_database_url,
    )

    platform_url = (
        "postgresql+asyncpg://postgres:secret@"
        "db.example:5432/platform"
    )

    monkeypatch.setenv(
        "DATABASE_URL",
        platform_url,
    )
    monkeypatch.setenv(
        "CONSTRUCTION_DATABASE_URL",
        construction_url,
    )

    with pytest.raises(
        ConstructionDatabaseConfigurationError
    ):
        get_construction_database_url()



def test_con_p007_reuses_platform_job_queue_contract():
    from services.job_queue import JobQueue
    from services.construction_jobs import (
        ConstructionJobQueue,
    )

    assert ConstructionJobQueue is JobQueue



def test_con_p007_exposes_postgresql_job_queue_adapter():
    import inspect

    from database.repositories.job_queue import (
        PostgresJobQueue,
    )

    assert "session" in inspect.signature(
        PostgresJobQueue
    ).parameters

    assert inspect.iscoroutinefunction(
        PostgresJobQueue.enqueue
    )
    assert inspect.iscoroutinefunction(
        PostgresJobQueue.claim_next
    )



@pytest.mark.asyncio
async def test_con_p007_claims_postgresql_job_atomically():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy import DateTime, Integer, Text, Uuid
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import (
        DeclarativeBase,
        mapped_column,
    )

    from database.repositories.job_queue import (
        PostgresJobQueue,
    )

    class QueueBase(DeclarativeBase):
        pass

    class QueueJob(QueueBase):
        __tablename__ = "test_background_jobs"

        id = mapped_column(
            Uuid,
            primary_key=True,
        )
        status = mapped_column(
            Text,
            nullable=False,
        )
        attempts = mapped_column(
            Integer,
            nullable=False,
        )
        available_at = mapped_column(
            DateTime(timezone=True),
            nullable=False,
        )
        lease_owner = mapped_column(
            Text,
            nullable=True,
        )
        lease_expires_at = mapped_column(
            DateTime(timezone=True),
            nullable=True,
        )

    now = datetime(
        2026,
        9,
        15,
        12,
        0,
        tzinfo=UTC,
    )
    lease_expires_at = now + timedelta(
        minutes=5
    )
    job = SimpleNamespace(
        id=uuid4(),
        status="QUEUED",
        attempts=0,
        available_at=now,
        lease_owner=None,
        lease_expires_at=None,
    )
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        async def flush(self):
            calls.append(("flush", None))

        async def commit(self):
            calls.append(("commit", None))

    queue = PostgresJobQueue(
        FakeSession(),
        model=QueueJob,
    )

    result = await queue.claim_next(
        now=now,
        lease_owner="construction-worker-01",
        lease_expires_at=lease_expires_at,
    )

    statement = calls[0][1]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "QUEUED" in sql
    assert "RETRY" in sql
    assert result is job
    assert job.status == "RUNNING"
    assert job.attempts == 1
    assert job.lease_owner == (
        "construction-worker-01"
    )
    assert job.lease_expires_at == lease_expires_at
    assert [
        name
        for name, _ in calls
    ] == [
        "execute",
        "flush",
    ]



def test_con_p007_construction_job_model_matches_confirmed_storage_contract():
    from database.models import (
        ConstructionBackgroundJobRecord,
    )

    table = ConstructionBackgroundJobRecord.__table__

    assert table.name == (
        "construction_background_jobs"
    )

    assert set(table.columns.keys()) >= {
        "id",
        "tenant_id",
        "user_id",
        "resource_type",
        "resource_id",
        "job_type",
        "status",
        "idempotency_key",
        "attempts",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "progress",
        "error_code",
        "result_reference",
        "created_at",
        "updated_at",
    }

    tenant_foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in table.c.tenant_id.foreign_keys
    }
    user_foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in table.c.user_id.foreign_keys
    }

    assert tenant_foreign_keys == {
        "tenants.id",
    }
    assert user_foreign_keys == {
        "users.id",
    }

    constraints = {
        constraint.name: str(
            constraint.sqltext
        )
        for constraint in table.constraints
        if constraint.name is not None
        and hasattr(constraint, "sqltext")
    }

    status_constraint = constraints[
        "ck_construction_background_jobs_status"
    ]

    for status in (
        "QUEUED",
        "RUNNING",
        "RETRY",
        "READY",
        "FAILED",
        "CANCELLED",
    ):
        assert status in status_constraint

    assert (
        "ck_construction_background_jobs_attempts"
        in constraints
    )
    assert (
        "ck_construction_background_jobs_progress"
        in constraints
    )



def test_con_p007_binds_postgresql_queue_to_construction_storage():
    from database.models import (
        ConstructionBackgroundJobRecord,
    )
    from database.repositories.job_queue import (
        PostgresJobQueue,
    )
    from services.construction_jobs import (
        build_construction_job_queue,
    )

    session = object()

    queue = build_construction_job_queue(
        session=session,
    )

    assert isinstance(queue, PostgresJobQueue)
    assert queue.session is session
    assert queue.model is (
        ConstructionBackgroundJobRecord
    )



@pytest.mark.asyncio
async def test_con_p007_enqueues_construction_job_without_internal_commit():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from services.construction_jobs import (
        ConstructionBackgroundJob,
        ConstructionJobStatus,
        ConstructionJobType,
        build_construction_job_queue,
    )

    job = ConstructionBackgroundJob(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        resource_type="estimate",
        resource_id=uuid4(),
        job_type=ConstructionJobType.EXPORT_PDF,
        status=ConstructionJobStatus.QUEUED,
        idempotency_key="estimate-export-001",
        attempts=0,
        available_at=datetime(
            2026,
            9,
            15,
            14,
            0,
            tzinfo=UTC,
        ),
        lease_owner=None,
        lease_expires_at=None,
        progress=0,
        error_code=None,
        result_reference=None,
    )
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job.id

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        async def commit(self):
            calls.append(("commit", None))

    queue = build_construction_job_queue(
        session=FakeSession(),
    )

    result = await queue.enqueue(job=job)

    sql = str(
        calls[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert result == job.id
    assert str(job.id).upper() in sql
    assert str(job.tenant_id).upper() in sql
    assert str(job.user_id).upper() in sql
    assert "'EXPORT_PDF'" in sql
    assert "'QUEUED'" in sql
    assert "'ESTIMATE-EXPORT-001'" in sql
    assert [
        name
        for name, _ in calls
    ] == [
        "execute",
    ]


@pytest.mark.asyncio
async def test_con_p007_reclaims_running_job_after_expired_lease():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.models import (
        ConstructionBackgroundJobRecord,
    )
    from database.repositories.job_queue import (
        PostgresJobQueue,
    )

    now = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=UTC,
    )
    new_lease = now + timedelta(minutes=5)

    job = SimpleNamespace(
        id=uuid4(),
        status="RUNNING",
        attempts=1,
        available_at=now - timedelta(minutes=10),
        lease_owner="stopped-worker",
        lease_expires_at=now - timedelta(minutes=1),
    )
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        async def flush(self):
            calls.append(("flush", None))

        async def commit(self):
            calls.append(("commit", None))

    queue = PostgresJobQueue(
        FakeSession(),
        model=ConstructionBackgroundJobRecord,
    )

    result = await queue.claim_next(
        now=now,
        lease_owner="replacement-worker",
        lease_expires_at=new_lease,
    )

    sql = str(
        calls[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "RUNNING" in sql
    assert "LEASE_EXPIRES_AT" in sql
    assert result is job
    assert job.status == "RUNNING"
    assert job.attempts == 2
    assert job.lease_owner == (
        "replacement-worker"
    )
    assert job.lease_expires_at == new_lease
    assert [
        name
        for name, _ in calls
    ] == [
        "execute",
        "flush",
    ]



@pytest.mark.asyncio
async def test_con_p007_updates_progress_only_for_current_lease_owner():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.models import (
        ConstructionBackgroundJobRecord,
    )
    from database.repositories.job_queue import (
        PostgresJobQueue,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        15,
        16,
        0,
        tzinfo=UTC,
    )
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job_id

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        async def commit(self):
            calls.append(("commit", None))

    queue = PostgresJobQueue(
        FakeSession(),
        model=ConstructionBackgroundJobRecord,
    )

    result = await queue.update_progress(
        job_id=job_id,
        lease_owner="construction-worker-01",
        progress=45,
        now=now,
    )

    sql = str(
        calls[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert result == job_id
    assert "UPDATE CONSTRUCTION_BACKGROUND_JOBS" in sql
    assert "PROGRESS=45" in sql
    assert "STATUS = 'RUNNING'" in sql
    assert "LEASE_OWNER = " in sql
    assert "CONSTRUCTION-WORKER-01" in sql
    assert calls == [
        ("execute", calls[0][1]),
    ]



@pytest.mark.asyncio
async def test_con_p007_marks_leased_job_ready_with_result_reference():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.models import (
        ConstructionBackgroundJobRecord,
    )
    from database.repositories.job_queue import (
        PostgresJobQueue,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        15,
        17,
        0,
        tzinfo=UTC,
    )
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job_id

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        async def commit(self):
            calls.append(("commit", None))

    queue = PostgresJobQueue(
        FakeSession(),
        model=ConstructionBackgroundJobRecord,
    )

    result = await queue.mark_ready(
        job_id=job_id,
        lease_owner="construction-worker-01",
        result_reference=(
            "organization/tenant/construction/"
            "projects/project/exports/result.pdf"
        ),
        now=now,
    )

    sql = str(
        calls[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert result == job_id
    assert "STATUS='READY'" in sql
    assert "PROGRESS=100" in sql
    assert "RESULT_REFERENCE=" in sql
    assert "STATUS = 'RUNNING'" in sql
    assert "LEASE_OWNER = " in sql
    assert "LEASE_EXPIRES_AT > " in sql
    assert "LEASE_OWNER=NULL" in sql
    assert "LEASE_EXPIRES_AT=NULL" in sql
    assert [
        name
        for name, _ in calls
    ] == [
        "execute",
    ]



@pytest.mark.asyncio
async def test_con_p007_schedules_retry_only_for_current_lease_owner():
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.models import (
        ConstructionBackgroundJobRecord,
    )
    from database.repositories.job_queue import (
        PostgresJobQueue,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        15,
        18,
        0,
        tzinfo=UTC,
    )
    available_at = now + timedelta(minutes=5)
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job_id

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        async def commit(self):
            calls.append(("commit", None))

    queue = PostgresJobQueue(
        FakeSession(),
        model=ConstructionBackgroundJobRecord,
    )

    result = await queue.schedule_retry(
        job_id=job_id,
        lease_owner="construction-worker-01",
        error_code="PROVIDER_UNAVAILABLE",
        available_at=available_at,
        now=now,
    )

    sql = str(
        calls[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert result == job_id
    assert "STATUS='RETRY'" in sql
    assert "AVAILABLE_AT=" in sql
    assert "ERROR_CODE='PROVIDER_UNAVAILABLE'" in sql
    assert "STATUS = 'RUNNING'" in sql
    assert "LEASE_OWNER = " in sql
    assert "LEASE_EXPIRES_AT > " in sql
    assert "LEASE_OWNER=NULL" in sql
    assert "LEASE_EXPIRES_AT=NULL" in sql
    assert [
        name
        for name, _ in calls
    ] == [
        "execute",
    ]



@pytest.mark.asyncio
async def test_con_p007_marks_leased_job_failed_with_machine_error_code():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.models import (
        ConstructionBackgroundJobRecord,
    )
    from database.repositories.job_queue import (
        PostgresJobQueue,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        15,
        19,
        0,
        tzinfo=UTC,
    )
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job_id

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        async def commit(self):
            calls.append(("commit", None))

    queue = PostgresJobQueue(
        FakeSession(),
        model=ConstructionBackgroundJobRecord,
    )

    result = await queue.mark_failed(
        job_id=job_id,
        lease_owner="construction-worker-01",
        error_code="EXPORT_FAILED",
        now=now,
    )

    sql = str(
        calls[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert result == job_id
    assert "STATUS='FAILED'" in sql
    assert "ERROR_CODE='EXPORT_FAILED'" in sql
    assert "RESULT_REFERENCE=NULL" in sql
    assert "STATUS = 'RUNNING'" in sql
    assert "LEASE_OWNER = " in sql
    assert "LEASE_EXPIRES_AT > " in sql
    assert "LEASE_OWNER=NULL" in sql
    assert "LEASE_EXPIRES_AT=NULL" in sql
    assert [
        name
        for name, _ in calls
    ] == [
        "execute",
    ]



@pytest.mark.asyncio
async def test_con_p007_cancels_only_unfinished_tenant_scoped_job():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.models import (
        ConstructionBackgroundJobRecord,
    )
    from database.repositories.job_queue import (
        PostgresJobQueue,
    )

    job_id = uuid4()
    tenant_id = uuid4()
    now = datetime(
        2026,
        9,
        15,
        20,
        0,
        tzinfo=UTC,
    )
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job_id

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        async def commit(self):
            calls.append(("commit", None))

    queue = PostgresJobQueue(
        FakeSession(),
        model=ConstructionBackgroundJobRecord,
    )

    result = await queue.cancel(
        job_id=job_id,
        tenant_id=tenant_id,
        now=now,
    )

    sql = str(
        calls[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert result == job_id
    assert "STATUS='CANCELLED'" in sql
    assert "TENANT_ID = " in sql
    assert str(tenant_id).upper() in sql
    assert "QUEUED" in sql
    assert "RETRY" in sql
    assert "RUNNING" in sql
    assert "LEASE_OWNER=NULL" in sql
    assert "LEASE_EXPIRES_AT=NULL" in sql
    assert [
        name
        for name, _ in calls
    ] == [
        "execute",
    ]



@pytest.mark.asyncio
async def test_con_p007_enqueues_with_atomic_postgresql_idempotency():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from services.construction_jobs import (
        ConstructionBackgroundJob,
        ConstructionJobStatus,
        ConstructionJobType,
        build_construction_job_queue,
    )

    job = ConstructionBackgroundJob(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        resource_type="estimate",
        resource_id=uuid4(),
        job_type=ConstructionJobType.EXPORT_XLSX,
        status=ConstructionJobStatus.QUEUED,
        idempotency_key="estimate-export-002",
        attempts=0,
        available_at=datetime(
            2026,
            9,
            15,
            21,
            0,
            tzinfo=UTC,
        ),
        lease_owner=None,
        lease_expires_at=None,
        progress=0,
        error_code=None,
        result_reference=None,
    )
    calls = []

    class FakeResult:
        def scalar_one_or_none(self):
            return job.id

    class FakeSession:
        async def execute(self, statement):
            calls.append(("execute", statement))
            return FakeResult()

        def add(self, record):
            calls.append(("add", record))

        async def flush(self):
            calls.append(("flush", None))

        async def commit(self):
            calls.append(("commit", None))

    queue = build_construction_job_queue(
        session=FakeSession(),
    )

    result = await queue.enqueue(job=job)

    assert queue.idempotency_columns == (
        "tenant_id",
        "user_id",
        "job_type",
        "idempotency_key",
    )

    sql = str(
        calls[0][1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert result == job.id
    assert (
        "INSERT INTO CONSTRUCTION_BACKGROUND_JOBS"
        in sql
    )
    assert (
        "ON CONFLICT "
        "(TENANT_ID, USER_ID, JOB_TYPE, IDEMPOTENCY_KEY) "
        "DO NOTHING"
        in sql
    )
    assert "RETURNING" in sql
    assert [
        name
        for name, _ in calls
    ] == [
        "execute",
    ]



@pytest.mark.asyncio
async def test_con_p007_replays_existing_job_for_same_idempotency_scope():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from services.construction_jobs import (
        ConstructionBackgroundJob,
        ConstructionJobStatus,
        ConstructionJobType,
        build_construction_job_queue,
    )

    requested_job_id = uuid4()
    existing_job_id = uuid4()
    tenant_id = uuid4()
    user_id = uuid4()

    job = ConstructionBackgroundJob(
        id=requested_job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type="estimate",
        resource_id=uuid4(),
        job_type=ConstructionJobType.EXPORT_PDF,
        status=ConstructionJobStatus.QUEUED,
        idempotency_key="same-export-request",
        attempts=0,
        available_at=datetime(
            2026,
            9,
            15,
            22,
            0,
            tzinfo=UTC,
        ),
        lease_owner=None,
        lease_expires_at=None,
        progress=0,
        error_code=None,
        result_reference=None,
    )
    existing_job = SimpleNamespace(
        id=existing_job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type=job.resource_type,
        resource_id=job.resource_id,
        job_type=job.job_type.value,
        idempotency_key=job.idempotency_key,
    )
    returned_values = [
        None,
        existing_job,
    ]
    statements = []

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult(
                returned_values.pop(0)
            )

        async def commit(self):
            raise AssertionError(
                "Queue must not commit internally."
            )

    queue = build_construction_job_queue(
        session=FakeSession(),
    )

    result = await queue.enqueue(job=job)

    assert result == existing_job_id
    assert len(statements) == 2

    insert_sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()
    lookup_sql = str(
        statements[1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert "ON CONFLICT" in insert_sql
    assert "SELECT" in lookup_sql
    assert str(tenant_id).upper() in lookup_sql
    assert str(user_id).upper() in lookup_sql
    assert "EXPORT_PDF" in lookup_sql
    assert "SAME-EXPORT-REQUEST" in lookup_sql



@pytest.mark.asyncio
async def test_con_p007_rejects_idempotency_key_reuse_for_different_job():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from database.repositories.job_queue import (
        JobIdempotencyConflictError,
    )
    from services.construction_jobs import (
        ConstructionBackgroundJob,
        ConstructionJobStatus,
        ConstructionJobType,
        build_construction_job_queue,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    job = ConstructionBackgroundJob(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type="estimate",
        resource_id=uuid4(),
        job_type=ConstructionJobType.EXPORT_PDF,
        status=ConstructionJobStatus.QUEUED,
        idempotency_key="reused-export-key",
        attempts=0,
        available_at=datetime(
            2026,
            9,
            15,
            23,
            0,
            tzinfo=UTC,
        ),
        lease_owner=None,
        lease_expires_at=None,
        progress=0,
        error_code=None,
        result_reference=None,
    )

    existing_job = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type="estimate",
        resource_id=uuid4(),
        job_type="EXPORT_PDF",
        idempotency_key="reused-export-key",
    )
    returned_values = [
        None,
        existing_job,
    ]

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        async def execute(self, statement):
            return FakeResult(
                returned_values.pop(0)
            )

        async def commit(self):
            raise AssertionError(
                "Queue must not commit internally."
            )

    queue = build_construction_job_queue(
        session=FakeSession(),
    )

    with pytest.raises(
        JobIdempotencyConflictError
    ):
        await queue.enqueue(job=job)



@pytest.mark.asyncio
async def test_con_p007_worker_dispatches_claimed_job_and_marks_result_ready():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_jobs import (
        ConstructionBackgroundJobWorker,
        ConstructionJobType,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        16,
        8,
        0,
        tzinfo=UTC,
    )
    job = SimpleNamespace(
        id=job_id,
        job_type="EXPORT_PDF",
    )
    calls = []

    class FakeQueue:
        async def claim_next(self, **kwargs):
            calls.append(("claim", kwargs))
            return job

        async def mark_ready(self, **kwargs):
            calls.append(("ready", kwargs))
            return kwargs["job_id"]

    async def export_pdf_handler(claimed_job):
        calls.append(("handler", claimed_job))
        return (
            "organization/tenant/construction/"
            "projects/project/exports/result.pdf"
        )

    worker = ConstructionBackgroundJobWorker(
        queue=FakeQueue(),
        handlers={
            ConstructionJobType.EXPORT_PDF.value: (
                export_pdf_handler
            ),
        },
        lease_owner="construction-worker-01",
        lease_duration=timedelta(minutes=5),
        clock=lambda: now,
    )

    result = await worker.run_once()

    assert result == job_id
    assert calls == [
        (
            "claim",
            {
                "now": now,
                "lease_owner": (
                    "construction-worker-01"
                ),
                "lease_expires_at": (
                    now + timedelta(minutes=5)
                ),
            },
        ),
        ("handler", job),
        (
            "ready",
            {
                "job_id": job_id,
                "lease_owner": (
                    "construction-worker-01"
                ),
                "result_reference": (
                    "organization/tenant/"
                    "construction/projects/project/"
                    "exports/result.pdf"
                ),
                "now": now,
            },
        ),
    ]



@pytest.mark.asyncio
async def test_con_p007_worker_schedules_explicit_retry_without_exposing_error_details():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_jobs import (
        ConstructionBackgroundJobWorker,
        ConstructionJobRetryError,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        16,
        9,
        0,
        tzinfo=UTC,
    )
    retry_at = now + timedelta(minutes=10)
    job = SimpleNamespace(
        id=job_id,
        job_type="SUPPLIER_SYNC",
    )
    calls = []

    class FakeQueue:
        async def claim_next(self, **kwargs):
            calls.append(("claim", kwargs))
            return job

        async def schedule_retry(self, **kwargs):
            calls.append(("retry", kwargs))
            return kwargs["job_id"]

    async def failing_handler(claimed_job):
        calls.append(("handler", claimed_job))
        raise ConstructionJobRetryError(
            error_code="PROVIDER_UNAVAILABLE",
            available_at=retry_at,
        )

    worker = ConstructionBackgroundJobWorker(
        queue=FakeQueue(),
        handlers={
            "SUPPLIER_SYNC": failing_handler,
        },
        lease_owner="construction-worker-02",
        lease_duration=timedelta(minutes=5),
        clock=lambda: now,
    )

    result = await worker.run_once()

    assert result == job_id
    assert calls[-1] == (
        "retry",
        {
            "job_id": job_id,
            "lease_owner": (
                "construction-worker-02"
            ),
            "error_code": (
                "PROVIDER_UNAVAILABLE"
            ),
            "available_at": retry_at,
            "now": now,
        },
    )

    assert "private" not in str(
        calls
    ).lower()



@pytest.mark.asyncio
async def test_con_p007_worker_marks_explicit_permanent_failure():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_jobs import (
        ConstructionBackgroundJobWorker,
        ConstructionJobPermanentError,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        16,
        10,
        0,
        tzinfo=UTC,
    )
    job = SimpleNamespace(
        id=job_id,
        job_type="BULK_IMPORT",
    )
    calls = []

    class FakeQueue:
        async def claim_next(self, **kwargs):
            calls.append(("claim", kwargs))
            return job

        async def mark_failed(self, **kwargs):
            calls.append(("failed", kwargs))
            return kwargs["job_id"]

    async def invalid_import_handler(claimed_job):
        calls.append(("handler", claimed_job))
        raise ConstructionJobPermanentError(
            error_code="IMPORT_VALIDATION_FAILED",
        )

    worker = ConstructionBackgroundJobWorker(
        queue=FakeQueue(),
        handlers={
            "BULK_IMPORT": invalid_import_handler,
        },
        lease_owner="construction-worker-03",
        lease_duration=timedelta(minutes=5),
        clock=lambda: now,
    )

    result = await worker.run_once()

    assert result == job_id
    assert calls[-1] == (
        "failed",
        {
            "job_id": job_id,
            "lease_owner": (
                "construction-worker-03"
            ),
            "error_code": (
                "IMPORT_VALIDATION_FAILED"
            ),
            "now": now,
        },
    )



@pytest.mark.asyncio
async def test_con_p007_worker_fails_closed_when_handler_is_missing():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_jobs import (
        ConstructionBackgroundJobWorker,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        16,
        11,
        0,
        tzinfo=UTC,
    )
    job = SimpleNamespace(
        id=job_id,
        job_type="UNKNOWN_JOB_TYPE",
    )
    calls = []

    class FakeQueue:
        async def claim_next(self, **kwargs):
            calls.append(("claim", kwargs))
            return job

        async def mark_failed(self, **kwargs):
            calls.append(("failed", kwargs))
            return kwargs["job_id"]

    worker = ConstructionBackgroundJobWorker(
        queue=FakeQueue(),
        handlers={},
        lease_owner="construction-worker-04",
        lease_duration=timedelta(minutes=5),
        clock=lambda: now,
    )

    result = await worker.run_once()

    assert result == job_id
    assert calls[-1] == (
        "failed",
        {
            "job_id": job_id,
            "lease_owner": (
                "construction-worker-04"
            ),
            "error_code": (
                "HANDLER_NOT_CONFIGURED"
            ),
            "now": now,
        },
    )



@pytest.mark.asyncio
async def test_con_p007_worker_sanitizes_unexpected_handler_failure():
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_jobs import (
        ConstructionBackgroundJobWorker,
    )

    job_id = uuid4()
    now = datetime(
        2026,
        9,
        16,
        12,
        0,
        tzinfo=UTC,
    )
    job = SimpleNamespace(
        id=job_id,
        job_type="EXPORT_PDF",
    )
    calls = []

    class FakeQueue:
        async def claim_next(self, **kwargs):
            calls.append(("claim", kwargs))
            return job

        async def mark_failed(self, **kwargs):
            calls.append(("failed", kwargs))
            return kwargs["job_id"]

    async def broken_handler(claimed_job):
        calls.append(("handler", claimed_job))
        raise RuntimeError(
            "Private provider credentials and response."
        )

    worker = ConstructionBackgroundJobWorker(
        queue=FakeQueue(),
        handlers={
            "EXPORT_PDF": broken_handler,
        },
        lease_owner="construction-worker-05",
        lease_duration=timedelta(minutes=5),
        clock=lambda: now,
    )

    result = await worker.run_once()

    assert result == job_id
    assert calls[-1] == (
        "failed",
        {
            "job_id": job_id,
            "lease_owner": (
                "construction-worker-05"
            ),
            "error_code": (
                "UNEXPECTED_JOB_ERROR"
            ),
            "now": now,
        },
    )
    assert "credentials" not in str(
        calls[-1]
    ).lower()
    assert "response" not in str(
        calls[-1]
    ).lower()



def test_con_p006_uses_supabase_upload_lifetime_and_short_download_ttl():
    from services.construction_storage import (
        CONSTRUCTION_SIGNED_DOWNLOAD_URL_TTL_SECONDS,
        CONSTRUCTION_SIGNED_UPLOAD_URL_TTL_SECONDS,
    )

    assert (
        CONSTRUCTION_SIGNED_UPLOAD_URL_TTL_SECONDS
        == 2 * 60 * 60
    )
    assert (
        CONSTRUCTION_SIGNED_DOWNLOAD_URL_TTL_SECONDS
        == 15 * 60
    )



@pytest.mark.asyncio
async def test_con_p006_rejects_paths_outside_confirmed_storage_convention():
    from uuid import uuid4

    from services.construction_storage import (
        ConstructionStorageAccessService,
        ConstructionStoragePathError,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    calls = []

    class FakeStorageProvider:
        async def create_signed_download_url(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return "https://storage.example/signed"

    service = ConstructionStorageAccessService(
        FakeStorageProvider()
    )

    invalid_paths = (
        (
            f"organization/{tenant_id}"
            "/construction/private/secret.pdf"
        ),
        (
            f"organization/{tenant_id}"
            f"/construction/projects/{project_id}"
            "/contracts/contract.pdf"
        ),
        (
            f"organization/{tenant_id}"
            "/construction/projects/not-a-uuid"
            "/measurements/photo.jpg"
        ),
    )

    for storage_path in invalid_paths:
        with pytest.raises(
            ConstructionStoragePathError
        ):
            await service.create_signed_download_url(
                actor_tenant_id=tenant_id,
                resource_tenant_id=tenant_id,
                storage_path=storage_path,
            )

    assert calls == []
