from construction_rate_limit_fixture import with_local_construction_rate_limit
from dataclasses import replace
from uuid import uuid4

import pytest

from services.api_identity import ApiActorContext
from services.construction.construction_permissions import (
    ConstructionAccessContext,
    ConstructionGrantContext,
)
from services.platform_company_profile_access import (
    PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    resolve_company_profile_permissions,
)


def make_actor(*, permissions=()):
    return ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=permissions,
    )


def make_access(actor, *, role, scope_type, scope_id):
    return ConstructionAccessContext(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        roles=(role,),
        permissions=(),
        grants=(
            ConstructionGrantContext(
                role=role,
                scope_type=scope_type,
                scope_id=scope_id,
            ),
        ),
    )


@pytest.mark.parametrize("role", ("OWNER", "ADMIN"))
def test_tenant_grant_resolves_default_platform_permission(role):
    actor = make_actor()
    access = make_access(
        actor,
        role=role,
        scope_type="tenant",
        scope_id=actor.tenant_id,
    )

    assert resolve_company_profile_permissions(
        actor=actor,
        construction_access_context=access,
    ) == frozenset({PLATFORM_ORGANIZATION_PROFILE_MANAGE})


def test_role_name_without_grant_does_not_grant_permission():
    actor = make_actor()
    access = ConstructionAccessContext(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        roles=("OWNER",),
        permissions=(),
        grants=(),
    )

    assert not resolve_company_profile_permissions(
        actor=actor,
        construction_access_context=access,
    )


def test_project_grant_does_not_grant_tenant_profile_permission():
    actor = make_actor()
    access = make_access(
        actor,
        role="OWNER",
        scope_type="project",
        scope_id=uuid4(),
    )

    assert not resolve_company_profile_permissions(
        actor=actor,
        construction_access_context=access,
    )


def test_other_construction_role_has_no_default_grant():
    actor = make_actor()
    access = make_access(
        actor,
        role="SURVEYOR",
        scope_type="project",
        scope_id=uuid4(),
    )

    assert not resolve_company_profile_permissions(
        actor=actor,
        construction_access_context=access,
    )


@pytest.mark.parametrize("field", ("user_id", "tenant_id"))
def test_mismatched_access_context_fails_closed(field):
    actor = make_actor()
    access = make_access(
        actor,
        role="OWNER",
        scope_type="tenant",
        scope_id=actor.tenant_id,
    )
    access = replace(access, **{field: uuid4()})

    assert not resolve_company_profile_permissions(
        actor=actor,
        construction_access_context=access,
    )


def test_explicit_platform_permission_is_accepted():
    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )

    assert resolve_company_profile_permissions(
        actor=actor,
    ) == frozenset({PLATFORM_ORGANIZATION_PROFILE_MANAGE})


def test_inactive_actor_cannot_use_explicit_permission():
    actor = replace(
        make_actor(
            permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
        ),
        status="suspended",
    )

    assert not resolve_company_profile_permissions(
        actor=actor,
    )


def test_profile_write_guard_denies_without_permission():
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
        require_company_profile_manage,
    )

    with pytest.raises(CompanyProfilePermissionDeniedError):
        require_company_profile_manage(actor=make_actor())


def test_profile_write_guard_accepts_resolved_permission():
    from services.platform_company_profile_access import (
        require_company_profile_manage,
    )

    actor = make_actor()
    access = make_access(
        actor,
        role="OWNER",
        scope_type="tenant",
        scope_id=actor.tenant_id,
    )
    assert require_company_profile_manage(
        actor=actor,
        construction_access_context=access,
    ) is None

    explicit_actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    assert require_company_profile_manage(
        actor=explicit_actor,
    ) is None


@pytest.mark.asyncio
async def test_con_c001_reads_inherited_profile_in_actor_tenant():
    from types import SimpleNamespace
    from services.construction.construction_company_profile import (
        ConstructionCompanyProfileService,
    )

    actor = make_actor()
    profile = SimpleNamespace(tenant_id=actor.tenant_id)
    calls = []

    class ProfileReader:
        async def get_for_tenant(self, *, tenant_id):
            calls.append(tenant_id)
            return profile

    result = await ConstructionCompanyProfileService(
        profile_reader=ProfileReader()
    ).get_inherited_profile(actor=actor)

    assert result is profile
    assert calls == [actor.tenant_id]


@pytest.mark.asyncio
async def test_con_c001_missing_profile_stays_absent():
    from services.construction.construction_company_profile import (
        ConstructionCompanyProfileService,
    )

    actor = make_actor()

    class ProfileReader:
        async def get_for_tenant(self, *, tenant_id):
            assert tenant_id == actor.tenant_id
            return None

    result = await ConstructionCompanyProfileService(
        profile_reader=ProfileReader()
    ).get_inherited_profile(actor=actor)

    assert result is None


@pytest.mark.asyncio
async def test_con_c001_rejects_profile_from_other_tenant():
    from types import SimpleNamespace
    from services.construction.construction_company_profile import (
        ConstructionCompanyProfileService,
        ConstructionCompanyProfileScopeError,
    )

    actor = make_actor()

    class ProfileReader:
        async def get_for_tenant(self, *, tenant_id):
            return SimpleNamespace(tenant_id=uuid4())

    with pytest.raises(ConstructionCompanyProfileScopeError):
        await ConstructionCompanyProfileService(
            profile_reader=ProfileReader()
        ).get_inherited_profile(actor=actor)


def test_con_c001_platform_profile_model_matches_confirmed_contract():
    from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint
    from database.models import TenantCompanyProfile

    table = TenantCompanyProfile.__table__
    assert table.name == "tenant_company_profiles"

    expected = {
        "id", "tenant_id", "legal_name", "commercial_name",
        "registered_address_raw", "operational_address_raw",
        "phone", "email", "website", "signatory_name",
        "signatory_position", "logo_file_id", "created_at",
        "updated_at", "row_version",
    }
    assert set(table.columns.keys()) == expected

    required = {
        "id", "tenant_id", "legal_name",
        "created_at", "updated_at", "row_version",
    }
    for name in expected:
        assert table.c[name].nullable == (name not in required)

    lengths = {
        "legal_name": 255,
        "commercial_name": 255,
        "phone": 50,
        "email": 320,
        "website": 2048,
        "signatory_name": 255,
        "signatory_position": 255,
    }
    for name, length in lengths.items():
        assert isinstance(table.c[name].type, String)
        assert table.c[name].type.length == length

    assert isinstance(table.c.row_version.type, BigInteger)
    assert TenantCompanyProfile.__mapper__.version_id_col is table.c.row_version
    for name in ("created_at", "updated_at"):
        assert isinstance(table.c[name].type, DateTime)
        assert table.c[name].type.timezone is True

    foreign_keys = {
        (fk.parent.name, fk.column.table.name, fk.column.name)
        for fk in table.foreign_keys
    }
    assert ("tenant_id", "tenants", "id") in foreign_keys
    assert ("logo_file_id", "file_storage_objects", "id") in foreign_keys

    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(column.name for column in constraint.columns)
        == ("tenant_id",)
        for constraint in table.constraints
    )


@pytest.mark.asyncio
async def test_con_c001_platform_repository_reads_only_requested_tenant():
    from database.repositories.company_profiles import (
        TenantCompanyProfileRepository,
    )

    tenant_id = uuid4()
    profile = object()
    executed = []

    class FakeResult:
        def scalar_one_or_none(self):
            return profile

    class FakeSession:
        async def execute(self, statement):
            executed.append(statement)
            return FakeResult()

    result = await TenantCompanyProfileRepository(
        FakeSession()
    ).get_for_tenant(tenant_id=tenant_id)

    assert result is profile
    assert len(executed) == 1
    statement = executed[0]
    assert (
        "tenant_company_profiles.tenant_id"
        in str(statement)
    )
    assert tenant_id in statement.compile().params.values()


def test_con_c001_profile_validation_preserves_email_local_case():
    from services.platform_company_profiles import (
        normalize_company_profile_fields,
    )

    result = normalize_company_profile_fields(
        legal_name="Example Company",
        email="  Sales.Team@Example.COM  ",
        website=None,
        phone=None,
    )

    assert result["email"] == result["email"].strip()
    assert result["email"].split("@", 1)[0] == "Sales.Team"


@pytest.mark.parametrize("legal_name", ("", "   ", "\t\n"))
def test_con_c001_profile_validation_rejects_blank_legal_name(
    legal_name,
):
    from services.platform_company_profiles import (
        CompanyProfileValidationError,
        normalize_company_profile_fields,
    )

    with pytest.raises(CompanyProfileValidationError):
        normalize_company_profile_fields(
            legal_name=legal_name,
            email=None,
            website=None,
            phone=None,
        )


@pytest.mark.parametrize(
    "website",
    (
        "example.com",
        "/company",
        "ftp://example.com",
        "https://",
    ),
)
def test_con_c001_profile_validation_requires_absolute_http_url(
    website,
):
    from services.platform_company_profiles import (
        CompanyProfileValidationError,
        normalize_company_profile_fields,
    )

    with pytest.raises(CompanyProfileValidationError):
        normalize_company_profile_fields(
            legal_name="Example Company",
            email=None,
            website=website,
            phone=None,
        )


def test_con_c001_profile_validation_rejects_invalid_email():
    from services.platform_company_profiles import (
        CompanyProfileValidationError,
        normalize_company_profile_fields,
    )

    with pytest.raises(CompanyProfileValidationError):
        normalize_company_profile_fields(
            legal_name="Example Company",
            email="not-an-email",
            website=None,
            phone=None,
        )


def test_con_c001_profile_validation_normalizes_international_phone():
    from services.platform_company_profiles import (
        normalize_company_profile_fields,
    )

    result = normalize_company_profile_fields(
        legal_name="Example Company",
        email=None,
        website=None,
        phone="+351 912 345 678",
    )

    assert result["phone"] == "+351912345678"


@pytest.mark.asyncio
async def test_con_c001_platform_repository_creates_without_commit():
    from database.models import TenantCompanyProfile
    from database.repositories.company_profiles import (
        TenantCompanyProfileRepository,
    )

    tenant_id = uuid4()

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flush_count = 0

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            self.flush_count += 1

    session = FakeSession()
    profile = await TenantCompanyProfileRepository(
        session
    ).create_profile(
        tenant_id=tenant_id,
        legal_name="Example Company",
        email="Sales.Team@example.com",
    )

    assert isinstance(profile, TenantCompanyProfile)
    assert profile.tenant_id == tenant_id
    assert profile.legal_name == "Example Company"
    assert profile.email == "Sales.Team@example.com"
    assert session.added == [profile]
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_con_c001_create_profile_audits_before_commit():
    from types import SimpleNamespace

    from services.platform_company_profile_access import (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    )
    from services.platform_company_profiles import (
        TenantCompanyProfileService,
    )

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    profile = SimpleNamespace(
        id=uuid4(),
        tenant_id=actor.tenant_id,
    )
    calls = []

    class FakeRepository:
        async def get_for_tenant(self, *, tenant_id):
            calls.append(("get", tenant_id))
            return None

        async def create_profile(self, **values):
            calls.append(("create", values))
            return profile

    class FakeEvents:
        async def create_event(self, **values):
            calls.append(("audit", values))

    class FakeSession:
        async def commit(self):
            calls.append(("commit", None))

        async def rollback(self):
            calls.append(("rollback", None))

    result = await TenantCompanyProfileService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEvents(),
    ).create_profile(
        actor=actor,
        legal_name="Example Company",
        email="  Sales.Team@Example.COM  ",
        website=None,
        phone=None,
        trace_id="profile-test-trace",
    )

    assert result is profile
    assert [name for name, _ in calls] == [
        "get", "create", "audit", "commit"
    ]
    assert calls[0][1] == actor.tenant_id
    assert calls[1][1]["tenant_id"] == actor.tenant_id
    assert calls[1][1]["email"].split("@", 1)[0] == "Sales.Team"
    audit = calls[2][1]
    assert audit["tenant_id"] == actor.tenant_id
    assert audit["user_id"] == actor.user_id
    assert audit["entity_id"] == profile.id
    assert audit["trace_id"] == "profile-test-trace"
    assert "iban" not in str(audit).lower()


@pytest.mark.asyncio
async def test_con_c001_create_profile_rolls_back_on_audit_failure():
    from types import SimpleNamespace

    from services.platform_company_profile_access import (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    )
    from services.platform_company_profiles import (
        TenantCompanyProfileService,
    )

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    calls = []

    class FakeRepository:
        async def get_for_tenant(self, *, tenant_id):
            return None

        async def create_profile(self, **values):
            calls.append("create")
            return SimpleNamespace(
                id=uuid4(),
                tenant_id=actor.tenant_id,
            )

    class FakeEvents:
        async def create_event(self, **values):
            calls.append("audit")
            raise RuntimeError("audit unavailable")

    class FakeSession:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    with pytest.raises(Exception):
        await TenantCompanyProfileService(
            session=FakeSession(),
            repository=FakeRepository(),
            event_repository=FakeEvents(),
        ).create_profile(
            actor=actor,
            legal_name="Example Company",
            email=None,
            website=None,
            phone=None,
        )

    assert calls == ["create", "audit", "rollback"]


@pytest.mark.asyncio
async def test_con_c001_create_profile_denies_without_permission():
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
    )
    from services.platform_company_profiles import (
        TenantCompanyProfileService,
    )

    class ForbiddenRepository:
        async def get_for_tenant(self, **kwargs):
            pytest.fail("Repository accessed without permission")

    class ForbiddenEvents:
        async def create_event(self, **kwargs):
            pytest.fail("Audit accessed without permission")

    class FakeSession:
        async def commit(self):
            pytest.fail("Committed without permission")

        async def rollback(self):
            pytest.fail("Rolled back before any DB access")

    with pytest.raises(CompanyProfilePermissionDeniedError):
        await TenantCompanyProfileService(
            session=FakeSession(),
            repository=ForbiddenRepository(),
            event_repository=ForbiddenEvents(),
        ).create_profile(
            actor=make_actor(),
            legal_name="Example Company",
            email=None,
            website=None,
            phone=None,
        )


@pytest.mark.asyncio
async def test_con_c001_create_profile_rejects_existing_profile():
    from services.platform_company_profile_access import (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    )
    from services.platform_company_profiles import (
        TenantCompanyProfileConflictError,
        TenantCompanyProfileService,
    )

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    calls = []

    class FakeRepository:
        async def get_for_tenant(self, *, tenant_id):
            calls.append(("get", tenant_id))
            return object()

        async def create_profile(self, **kwargs):
            pytest.fail("Duplicate profile created")

    class FakeEvents:
        async def create_event(self, **kwargs):
            pytest.fail("Duplicate profile audited as creation")

    class FakeSession:
        async def commit(self):
            pytest.fail("Duplicate profile committed")

        async def rollback(self):
            calls.append(("rollback", None))

    with pytest.raises(TenantCompanyProfileConflictError):
        await TenantCompanyProfileService(
            session=FakeSession(),
            repository=FakeRepository(),
            event_repository=FakeEvents(),
        ).create_profile(
            actor=actor,
            legal_name="Example Company",
            email=None,
            website=None,
            phone=None,
        )

    assert calls == [
        ("get", actor.tenant_id),
        ("rollback", None),
    ]


@pytest.mark.asyncio
async def test_con_c001_create_profile_rejects_foreign_tenant_result():
    from types import SimpleNamespace

    from services.platform_company_profile_access import (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    )
    from services.platform_company_profiles import (
        TenantCompanyProfileOperationError,
        TenantCompanyProfileService,
    )

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    calls = []

    class FakeRepository:
        async def get_for_tenant(self, *, tenant_id):
            calls.append("get")
            return None

        async def create_profile(self, **kwargs):
            calls.append("create")
            return SimpleNamespace(
                id=uuid4(),
                tenant_id=uuid4(),
            )

    class FakeEvents:
        async def create_event(self, **kwargs):
            pytest.fail("Foreign tenant profile audited")

    class FakeSession:
        async def commit(self):
            pytest.fail("Foreign tenant profile committed")

        async def rollback(self):
            calls.append("rollback")

    with pytest.raises(TenantCompanyProfileOperationError):
        await TenantCompanyProfileService(
            session=FakeSession(),
            repository=FakeRepository(),
            event_repository=FakeEvents(),
        ).create_profile(
            actor=actor,
            legal_name="Example Company",
            email=None,
            website=None,
            phone=None,
        )

    assert calls == ["get", "create", "rollback"]


@pytest.mark.asyncio
async def test_con_c001_repository_locks_tenant_profile_and_refreshes_state():
    from sqlalchemy.dialects import postgresql

    from database.repositories.company_profiles import (
        TenantCompanyProfileRepository,
    )

    tenant_id = uuid4()
    profile = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return profile

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult()

    result = await TenantCompanyProfileRepository(
        FakeSession()
    ).get_for_tenant_for_update(
        tenant_id=tenant_id
    )

    assert result is profile
    assert len(statements) == 1

    statement = statements[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).upper()

    assert "FOR UPDATE" in sql
    assert "TENANT_COMPANY_PROFILES.TENANT_ID" in sql
    assert tenant_id in compiled.params.values()
    assert (
        statement.get_execution_options().get("populate_existing")
        is True
    )


@pytest.mark.asyncio
async def test_con_c001_update_rejects_stale_row_version_before_mutation():
    from types import SimpleNamespace

    from services.platform_company_profile_access import (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    )
    from services.platform_company_profiles import (
        CompanyProfileRowVersionConflictError,
        TenantCompanyProfileService,
    )

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    profile = SimpleNamespace(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        row_version=3,
        legal_name="Current legal name",
    )
    calls = []

    class FakeRepository:
        async def get_for_tenant_for_update(self, *, tenant_id):
            calls.append(("lock", tenant_id))
            return profile

        async def update_profile(self, **kwargs):
            pytest.fail("Stale request mutated profile")

    class FakeEvents:
        async def create_event(self, **kwargs):
            pytest.fail("Stale request wrote audit")

    class FakeSession:
        async def commit(self):
            pytest.fail("Stale request committed")

        async def rollback(self):
            calls.append(("rollback", None))

    with pytest.raises(
        CompanyProfileRowVersionConflictError
    ) as error:
        await TenantCompanyProfileService(
            session=FakeSession(),
            repository=FakeRepository(),
            event_repository=FakeEvents(),
        ).update_profile(
            actor=actor,
            expected_row_version=2,
            changes={"legal_name": "New legal name"},
        )

    assert error.value.status_code == 409
    assert error.value.code == "ROW_VERSION_CONFLICT"
    assert calls == [
        ("lock", actor.tenant_id),
        ("rollback", None),
    ]
    assert profile.legal_name == "Current legal name"


@pytest.mark.asyncio
async def test_con_c001_update_audits_before_commit_without_field_values():
    from types import SimpleNamespace

    from services.platform_company_profile_access import (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    )
    from services.platform_company_profiles import TenantCompanyProfileService

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    profile = SimpleNamespace(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        row_version=1,
        legal_name="Old legal name",
    )
    calls = []

    class FakeRepository:
        async def get_for_tenant_for_update(self, *, tenant_id):
            calls.append("lock")
            assert tenant_id == actor.tenant_id
            return profile

        async def update_profile(self, *, profile, values):
            calls.append("update")
            assert values == {"legal_name": "New legal name"}
            profile.legal_name = values["legal_name"]
            return profile

    class FakeEvents:
        async def create_event(self, **kwargs):
            calls.append("audit")
            assert kwargs["tenant_id"] == actor.tenant_id
            assert kwargs["user_id"] == actor.user_id
            assert kwargs["entity_id"] == profile.id
            assert kwargs["event_type"] == "platform_company_profile_updated"
            assert kwargs["trace_id"] == "profile-update-test"
            assert kwargs["payload"] == {
                "operation": "update",
                "fields": ["legal_name"],
            }
            assert "New legal name" not in str(kwargs["payload"])

    class FakeSession:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            pytest.fail("Successful update rolled back")

    result = await TenantCompanyProfileService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEvents(),
    ).update_profile(
        actor=actor,
        expected_row_version=1,
        changes={"legal_name": "New legal name"},
        trace_id="profile-update-test",
    )

    assert result is profile
    assert calls == ["lock", "update", "audit", "commit"]


@pytest.mark.asyncio
async def test_con_c001_update_rolls_back_when_audit_fails():
    from types import SimpleNamespace

    from services.platform_company_profile_access import (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    )
    from services.platform_company_profiles import (
        TenantCompanyProfileOperationError,
        TenantCompanyProfileService,
    )

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    profile = SimpleNamespace(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        row_version=1,
        legal_name="Old legal name",
    )
    calls = []

    class FakeRepository:
        async def get_for_tenant_for_update(self, *, tenant_id):
            calls.append("lock")
            assert tenant_id == actor.tenant_id
            return profile

        async def update_profile(self, *, profile, values):
            calls.append("update")
            profile.legal_name = values["legal_name"]
            return profile

    class FakeEvents:
        async def create_event(self, **kwargs):
            calls.append("audit")
            raise RuntimeError("Private audit storage failure")

    class FakeSession:
        async def commit(self):
            pytest.fail("Update committed without audit")

        async def rollback(self):
            calls.append("rollback")

    with pytest.raises(TenantCompanyProfileOperationError) as error:
        await TenantCompanyProfileService(
            session=FakeSession(),
            repository=FakeRepository(),
            event_repository=FakeEvents(),
        ).update_profile(
            actor=actor,
            expected_row_version=1,
            changes={"legal_name": "New legal name"},
        )

    assert "Private audit storage failure" not in str(error.value)
    assert calls == ["lock", "update", "audit", "rollback"]


@pytest.mark.asyncio
async def test_con_c001_unchanged_update_skips_mutation_and_audit():
    from types import SimpleNamespace

    from services.platform_company_profile_access import (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE,
    )
    from services.platform_company_profiles import TenantCompanyProfileService

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    profile = SimpleNamespace(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        row_version=4,
        legal_name="Same legal name",
    )
    calls = []

    class FakeRepository:
        async def get_for_tenant_for_update(self, *, tenant_id):
            calls.append("lock")
            assert tenant_id == actor.tenant_id
            return profile

        async def update_profile(self, **kwargs):
            pytest.fail("Unchanged profile was updated")

    class FakeEvents:
        async def create_event(self, **kwargs):
            pytest.fail("Unchanged profile was audited")

    class FakeSession:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            pytest.fail("Unchanged update rolled back")

    result = await TenantCompanyProfileService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEvents(),
    ).update_profile(
        actor=actor,
        expected_row_version=4,
        changes={"legal_name": "Same legal name"},
    )

    assert result is profile
    assert profile.row_version == 4
    assert calls == ["lock", "commit"]


@pytest.mark.asyncio
async def test_con_c001_company_profile_writes_use_platform_api_namespace():
    import httpx

    from api.app import create_app

    application = with_local_construction_rate_limit(create_app())
    transport = httpx.ASGITransport(
        app=application,
        raise_app_exceptions=False,
    )

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        create_response = await client.post(
            "/api/v1/organization/company-profile",
            json={"legal_name": "Example Company"},
        )
        update_response = await client.patch(
            "/api/v1/organization/company-profile",
            json={
                "expected_row_version": 1,
                "legal_name": "Example Company",
            },
        )
        construction_response = await client.post(
            "/api/v1/construction/company-profile",
            json={"legal_name": "Example Company"},
        )

    assert create_response.status_code == 401
    assert update_response.status_code == 401
    assert construction_response.status_code == 404


@pytest.mark.asyncio
async def test_con_c001_write_authorization_uses_verified_tenant_grant():
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
        authorize_company_profile_write,
    )

    actor = make_actor()
    tenant_access = make_access(
        actor,
        role="OWNER",
        scope_type="tenant",
        scope_id=actor.tenant_id,
    )
    project_access = make_access(
        actor,
        role="OWNER",
        scope_type="project",
        scope_id=uuid4(),
    )
    calls = []

    class Resolver:
        def __init__(self, context):
            self.context = context

        async def resolve(self, *, actor):
            calls.append(actor.user_id)
            return self.context

    result = await authorize_company_profile_write(
        actor=actor,
        access_context_resolver=Resolver(tenant_access),
    )
    assert result is tenant_access

    with pytest.raises(CompanyProfilePermissionDeniedError):
        await authorize_company_profile_write(
            actor=actor,
            access_context_resolver=Resolver(project_access),
        )

    explicit_actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    assert await authorize_company_profile_write(
        actor=explicit_actor,
    ) is None
    assert calls == [actor.user_id, actor.user_id]


@pytest.mark.asyncio
async def test_con_c001_platform_profile_http_denies_write_without_permission(
    monkeypatch,
):
    import importlib
    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
    )

    route_module = importlib.import_module(
        "api.routes.company_profiles"
    )
    actor = make_actor()
    calls = []

    async def deny_access(*, actor):
        calls.append(("guard", actor.user_id))
        raise CompanyProfilePermissionDeniedError(
            "Permission is not available."
        )

    def unexpected_service():
        pytest.fail("Denied request reached company profile service")

    monkeypatch.setattr(
        route_module,
        "load_company_profile_write_context",
        deny_access,
    )

    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        route_module.router.dependencies[0].dependency
    ] = lambda: actor
    application.dependency_overrides[
        route_module.get_company_profile_service
    ] = unexpected_service

    transport = httpx.ASGITransport(
        app=application,
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        responses = (
            await client.post(
                "/api/v1/organization/company-profile",
                json={"legal_name": "Example Company"},
            ),
            await client.patch(
                "/api/v1/organization/company-profile",
                json={
                    "expected_row_version": 1,
                    "legal_name": "Example Company",
                },
            ),
        )

    assert [response.status_code for response in responses] == [403, 403]
    assert all(
        response.json()["error"]["code"] == "forbidden"
        for response in responses
    )
    assert calls == [
        ("guard", actor.user_id),
        ("guard", actor.user_id),
    ]


@pytest.mark.asyncio
async def test_con_c001_platform_profile_http_creates_in_actor_scope(
    monkeypatch,
):
    import importlib
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor

    route_module = importlib.import_module(
        "api.routes.company_profiles"
    )
    actor = make_actor()
    access = make_access(
        actor,
        role="OWNER",
        scope_type="tenant",
        scope_id=actor.tenant_id,
    )
    calls = []
    now = datetime.now(UTC)
    profile_id = uuid4()

    async def allow_access(*, actor):
        calls.append(("guard", actor.user_id))
        return access

    class FakeService:
        async def create_profile(self, **kwargs):
            calls.append(("create", kwargs))
            assert kwargs["actor"] is actor
            assert kwargs["construction_access_context"] is access
            assert kwargs["legal_name"] == "Example Company"
            assert kwargs["logo_file_id"] is None
            assert isinstance(kwargs["trace_id"], str)
            assert kwargs["trace_id"]
            return SimpleNamespace(
                id=profile_id,
                tenant_id=actor.tenant_id,
                legal_name="Example Company",
                commercial_name=None,
                registered_address_raw=None,
                operational_address_raw=None,
                phone=None,
                email=None,
                website=None,
                signatory_name=None,
                signatory_position=None,
                logo_file_id=None,
                created_at=now,
                updated_at=now,
                row_version=1,
            )

    monkeypatch.setattr(
        route_module,
        "load_company_profile_write_context",
        allow_access,
    )

    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        route_module.router.dependencies[0].dependency
    ] = lambda: actor
    application.dependency_overrides[
        route_module.get_company_profile_service
    ] = lambda: FakeService()

    transport = httpx.ASGITransport(
        app=application,
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/organization/company-profile",
            json={"legal_name": "Example Company"},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data"]["id"] == str(profile_id)
    assert body["data"]["tenant_id"] == str(actor.tenant_id)
    assert body["data"]["row_version"] == 1
    assert body["request_id"] == calls[1][1]["trace_id"]
    assert [item[0] for item in calls] == ["guard", "create"]


@pytest.mark.asyncio
async def test_con_c001_platform_profile_http_returns_409_for_stale_version(
    monkeypatch,
):
    import importlib

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from services.platform_company_profiles import (
        CompanyProfileRowVersionConflictError,
    )

    route_module = importlib.import_module(
        "api.routes.company_profiles"
    )
    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    calls = []

    async def allow_access(*, actor):
        calls.append(("guard", actor.user_id))
        return None

    class FakeService:
        async def update_profile(self, **kwargs):
            calls.append(("update", kwargs))
            assert kwargs["actor"] is actor
            assert kwargs["construction_access_context"] is None
            assert kwargs["expected_row_version"] == 2
            assert kwargs["changes"] == {
                "legal_name": "Changed Company"
            }
            raise CompanyProfileRowVersionConflictError()

    monkeypatch.setattr(
        route_module,
        "load_company_profile_write_context",
        allow_access,
    )

    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        route_module.router.dependencies[0].dependency
    ] = lambda: actor
    application.dependency_overrides[
        route_module.get_company_profile_service
    ] = lambda: FakeService()

    transport = httpx.ASGITransport(
        app=application,
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.patch(
            "/api/v1/organization/company-profile",
            json={
                "expected_row_version": 2,
                "legal_name": "Changed Company",
            },
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ROW_VERSION_CONFLICT"
    assert "Private" not in response.text
    assert [item[0] for item in calls] == ["guard", "update"]


@pytest.mark.asyncio
async def test_con_c001_platform_profile_http_updates_with_row_version(
    monkeypatch,
):
    import importlib
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor

    route_module = importlib.import_module(
        "api.routes.company_profiles"
    )
    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    calls = []
    now = datetime.now(UTC)

    async def allow_access(*, actor):
        calls.append("guard")
        return None

    class FakeService:
        async def update_profile(self, **kwargs):
            calls.append("update")
            assert kwargs["actor"] is actor
            assert kwargs["construction_access_context"] is None
            assert kwargs["expected_row_version"] == 1
            assert kwargs["changes"] == {
                "commercial_name": "New brand"
            }
            assert kwargs["trace_id"]
            return SimpleNamespace(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                legal_name="Example Company",
                commercial_name="New brand",
                registered_address_raw=None,
                operational_address_raw=None,
                phone=None,
                email=None,
                website=None,
                signatory_name=None,
                signatory_position=None,
                logo_file_id=None,
                created_at=now,
                updated_at=now,
                row_version=2,
            )

    monkeypatch.setattr(
        route_module,
        "load_company_profile_write_context",
        allow_access,
    )
    application = with_local_construction_rate_limit(create_app())
    application.dependency_overrides[get_current_actor] = lambda: actor
    application.dependency_overrides[
        route_module.router.dependencies[0].dependency
    ] = lambda: actor
    application.dependency_overrides[
        route_module.get_company_profile_service
    ] = lambda: FakeService()

    transport = httpx.ASGITransport(
        app=application,
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.patch(
            "/api/v1/organization/company-profile",
            json={
                "expected_row_version": 1,
                "commercial_name": "New brand",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["tenant_id"] == str(actor.tenant_id)
    assert response.json()["data"]["commercial_name"] == "New brand"
    assert response.json()["data"]["row_version"] == 2
    assert calls == ["guard", "update"]


@pytest.mark.asyncio
async def test_con_c001_concurrent_create_maps_tenant_unique_violation_to_conflict():
    from sqlalchemy.exc import IntegrityError

    from services.platform_company_profiles import (
        TenantCompanyProfileConflictError,
        TenantCompanyProfileService,
    )

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    calls = []

    class DuplicateTenant(Exception):
        sqlstate = "23505"
        constraint_name = "uq_tenant_company_profiles_tenant_id"

    class FakeRepository:
        async def get_for_tenant(self, *, tenant_id):
            calls.append("get")
            assert tenant_id == actor.tenant_id
            return None

        async def create_profile(self, **kwargs):
            calls.append("insert")
            raise IntegrityError(
                "INSERT INTO tenant_company_profiles",
                {},
                DuplicateTenant("duplicate tenant profile"),
            )

    class FakeEvents:
        async def create_event(self, **kwargs):
            pytest.fail("Rejected duplicate wrote audit")

    class FakeSession:
        async def commit(self):
            pytest.fail("Rejected duplicate committed")

        async def rollback(self):
            calls.append("rollback")

    with pytest.raises(TenantCompanyProfileConflictError):
        await TenantCompanyProfileService(
            session=FakeSession(),
            repository=FakeRepository(),
            event_repository=FakeEvents(),
        ).create_profile(
            actor=actor,
            legal_name="Example Company",
            email=None,
            website=None,
            phone=None,
        )

    assert calls == ["get", "insert", "rollback"]


@pytest.mark.asyncio
async def test_con_c001_unrelated_integrity_error_is_not_profile_conflict():
    from sqlalchemy.exc import IntegrityError

    from services.platform_company_profiles import (
        TenantCompanyProfileOperationError,
        TenantCompanyProfileService,
    )

    actor = make_actor(
        permissions=(PLATFORM_ORGANIZATION_PROFILE_MANAGE,)
    )
    calls = []

    class OtherConstraint(Exception):
        sqlstate = "23505"
        constraint_name = "some_other_unique_constraint"

    class FakeRepository:
        async def get_for_tenant(self, *, tenant_id):
            return None

        async def create_profile(self, **kwargs):
            raise IntegrityError(
                "INSERT INTO tenant_company_profiles",
                {},
                OtherConstraint("Private database detail"),
            )

    class FakeEvents:
        async def create_event(self, **kwargs):
            pytest.fail("Failed insert wrote audit")

    class FakeSession:
        async def commit(self):
            pytest.fail("Failed insert committed")

        async def rollback(self):
            calls.append("rollback")

    with pytest.raises(TenantCompanyProfileOperationError) as error:
        await TenantCompanyProfileService(
            session=FakeSession(),
            repository=FakeRepository(),
            event_repository=FakeEvents(),
        ).create_profile(
            actor=actor,
            legal_name="Example Company",
            email=None,
            website=None,
            phone=None,
        )

    assert "Private database detail" not in str(error.value)
    assert calls == ["rollback"]


@pytest.mark.asyncio
async def test_con_c003_inherits_legal_identity_without_fallback():
    from types import SimpleNamespace

    from services.construction.construction_company_profile import (
        ConstructionCompanyProfileScopeError,
        ConstructionCompanyProfileService,
    )

    actor = make_actor()
    profile = SimpleNamespace(
        tenant_id=actor.tenant_id,
        legal_name="Example Legal Ltd",
        commercial_name="Example",
        registered_address_raw="Registered address",
        operational_address_raw=None,
        signatory_name="Jane Doe",
        signatory_position="Director",
    )

    class Reader:
        def __init__(self, result):
            self.result = result

        async def get_for_tenant(self, *, tenant_id):
            assert tenant_id == actor.tenant_id
            return self.result

    service = ConstructionCompanyProfileService(
        profile_reader=Reader(profile)
    )
    assert await service.get_legal_identity(actor=actor) == {
        "legal_name": "Example Legal Ltd",
        "commercial_name": "Example",
        "registered_address_raw": "Registered address",
        "operational_address_raw": None,
        "signatory_name": "Jane Doe",
        "signatory_position": "Director",
    }

    missing = ConstructionCompanyProfileService(
        profile_reader=Reader(None)
    )
    assert await missing.get_legal_identity(actor=actor) is None

    foreign = ConstructionCompanyProfileService(
        profile_reader=Reader(
            SimpleNamespace(tenant_id=uuid4())
        )
    )
    with pytest.raises(ConstructionCompanyProfileScopeError):
        await foreign.get_legal_identity(actor=actor)


def test_con_c003_bank_account_model_matches_confirmed_contract():
    from sqlalchemy import BigInteger, CHAR, CheckConstraint

    from database.models import TenantCompanyBankAccount

    table = TenantCompanyBankAccount.__table__
    assert table.name == "tenant_company_bank_accounts"
    assert set(table.columns.keys()) == {
        "id", "tenant_company_profile_id", "label",
        "account_holder_name", "bank_name", "country_code",
        "iban", "bic_swift", "account_number", "currency",
        "is_primary", "is_active", "show_in_documents",
        "created_at", "updated_at", "row_version",
    }
    assert table.c.id.primary_key
    assert table.c.tenant_company_profile_id.nullable is False
    assert table.c.account_holder_name.nullable is False
    assert table.c.currency.nullable is False
    assert isinstance(table.c.country_code.type, CHAR)
    assert table.c.country_code.type.length == 2
    assert isinstance(table.c.currency.type, CHAR)
    assert table.c.currency.type.length == 3
    assert isinstance(table.c.row_version.type, BigInteger)
    assert (
        TenantCompanyBankAccount.__mapper__.version_id_col
        is table.c.row_version
    )
    assert any(
        fk.target_fullname == "tenant_company_profiles.id"
        for fk in table.c.tenant_company_profile_id.foreign_keys
    )
    assert any(
        isinstance(constraint, CheckConstraint)
        and "iban" in str(constraint.sqltext).lower()
        and "account_number" in str(constraint.sqltext).lower()
        for constraint in table.constraints
    )
    assert any(
        index.unique
        and tuple(column.name for column in index.columns)
        == ("tenant_company_profile_id", "currency")
        and "is_active" in str(
            index.dialect_options["postgresql"]["where"]
        )
        and "is_primary" in str(
            index.dialect_options["postgresql"]["where"]
        )
        for index in table.indexes
    )


@pytest.mark.asyncio
async def test_con_c003_document_bank_accounts_require_both_flags():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.company_profiles import (
        TenantCompanyBankAccountRepository,
    )

    tenant_id = uuid4()
    accounts = [object(), object()]
    statements = []

    class Result:
        def scalars(self):
            return self

        def all(self):
            return accounts

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return Result()

    result = await TenantCompanyBankAccountRepository(
        Session()
    ).list_document_visible_for_tenant(tenant_id=tenant_id)

    assert result == accounts
    assert len(statements) == 1
    compiled = statements[0].compile(
        dialect=postgresql.dialect()
    )
    sql = str(compiled).lower()
    assert "join tenant_company_profiles" in sql
    assert "tenant_company_profiles.tenant_id" in sql
    assert "tenant_company_bank_accounts.is_active is true" in sql
    assert (
        "tenant_company_bank_accounts.show_in_documents is true"
        in sql
    )
    assert tenant_id in compiled.params.values()


def test_con_c003_document_bank_projection_excludes_hidden_fields():
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from services.platform_company_bank_accounts import (
        BankAccountNotDocumentVisibleError,
        project_document_bank_account,
    )

    values = {
        "id": uuid4(),
        "label": "Internal label",
        "account_holder_name": "Example Legal Ltd",
        "bank_name": "Example Bank",
        "iban": "PT50000201231234567890154",
        "account_number": None,
        "bic_swift": "EXAMPLE1",
        "currency": "EUR",
        "is_active": True,
        "show_in_documents": True,
        "private_note": "Must never appear in document output",
    }

    projected = project_document_bank_account(
        account=SimpleNamespace(**values)
    )
    assert projected == {
        "account_holder_name": "Example Legal Ltd",
        "bank_name": "Example Bank",
        "iban": "PT50000201231234567890154",
        "account_number": None,
        "bic_swift": "EXAMPLE1",
        "currency": "EUR",
    }
    assert "id" not in projected
    assert "label" not in projected
    assert "private_note" not in projected

    for changes in (
        {"is_active": False},
        {"show_in_documents": False},
    ):
        with pytest.raises(BankAccountNotDocumentVisibleError):
            project_document_bank_account(
                account=SimpleNamespace(**(values | changes))
            )
