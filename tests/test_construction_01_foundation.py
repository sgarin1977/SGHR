from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_con_001_repository_reuses_active_platform_tenant():
    from sqlalchemy.dialects import postgresql

    from database.models import Tenant
    from database.repositories.construction import (
        ConstructionOrganizationRepository,
    )

    tenant_id = uuid4()
    expected = SimpleNamespace(
        id=tenant_id,
        status="active",
    )

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
    repository = ConstructionOrganizationRepository(
        session
    )

    result = await repository.get_active_organization(
        tenant_id=tenant_id,
    )

    assert result is expected
    assert Tenant.__tablename__ == "tenants"

    statement = session.statements[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized_sql = " ".join(
        sql.lower().split()
    )

    assert "from tenants" in normalized_sql
    assert "tenants.id =" in normalized_sql
    assert (
        "tenants.status = 'active'"
        in normalized_sql
    )
    assert (
        "construction_organizations"
        not in normalized_sql
    )



@pytest.mark.asyncio
async def test_con_001_service_uses_verified_actor_organization():
    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_foundation import (
        ConstructionOrganizationContext,
        ConstructionOrganizationService,
    )

    tenant_id = uuid4()
    calls = []

    platform_tenant = SimpleNamespace(
        id=tenant_id,
        name="Example Construction",
        slug="example-construction",
        default_language="ru",
        default_currency="EUR",
        status="active",
    )

    class FakeRepository:
        async def get_active_organization(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return platform_tenant

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=tenant_id,
        active_role="owner",
        roles=("owner",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
    )

    service = ConstructionOrganizationService(
        repository=FakeRepository()
    )

    result = await service.require_current(
        actor=actor,
    )

    assert result == (
        ConstructionOrganizationContext(
            tenant_id=tenant_id,
            name="Example Construction",
            slug="example-construction",
            default_language="ru",
            default_currency="EUR",
        )
    )
    assert calls == [
        {
            "tenant_id": tenant_id,
        }
    ]



@pytest.mark.asyncio
async def test_con_001_service_rejects_unavailable_or_foreign_platform_tenant():
    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_foundation import (
        ConstructionOrganizationService,
        ConstructionOrganizationUnavailableError,
    )

    tenant_id = uuid4()
    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=tenant_id,
        active_role="owner",
        roles=("owner",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
    )

    organizations = (
        None,
        SimpleNamespace(
            id=uuid4(),
            status="active",
        ),
        SimpleNamespace(
            id=tenant_id,
            status="suspended",
        ),
    )

    for organization in organizations:
        calls = []

        class FakeRepository:
            async def get_active_organization(
                self,
                **kwargs,
            ):
                calls.append(kwargs)
                return organization

        service = ConstructionOrganizationService(
            repository=FakeRepository()
        )

        with pytest.raises(
            ConstructionOrganizationUnavailableError
        ):
            await service.require_current(
                actor=actor,
            )

        assert calls == [
            {
                "tenant_id": tenant_id,
            }
        ]



def test_con_002_defines_confirmed_permission_and_scope_registry():
    from services.construction_permissions import (
        CONSTRUCTION_PERMISSION_CODES,
        CONSTRUCTION_SCOPE_TYPES,
    )

    assert CONSTRUCTION_SCOPE_TYPES == frozenset(
        {
            "tenant",
            "project",
            "document",
        }
    )

    assert CONSTRUCTION_PERMISSION_CODES == frozenset(
        {
            "construction.projects.read",
            "construction.projects.create",
            "construction.projects.edit",
            "construction.measurements.read",
            "construction.measurements.create",
            "construction.measurements.edit",
            "construction.measurements.submit",
            "construction.measurements.verify",
            "construction.estimates.read",
            "construction.estimates.create",
            "construction.estimates.edit",
            "construction.estimates.submit",
            "construction.estimates.approve",
            "construction.pricing.read",
            "construction.pricing.edit",
            "construction.costs.read",
            "construction.costs.edit",
            "construction.materials.read",
            "construction.materials.edit",
            "construction.tax.read",
            "construction.tax.edit",
            "construction.documents.generate",
            "construction.exports.internal",
            "construction.exports.client",
            "construction.exports.procurement",
            "construction.team.manage",
            "construction.access.manage",
            "construction.logistics.read",
            "construction.logistics.edit",
            "construction.planning.read",
            "construction.planning.edit",
            "construction.planning.approve",
            "construction.workforce.read",
            "construction.workforce.assign",
            "construction.workforce.crews.read",
            "construction.workforce.crews.manage",
            "construction.workforce.cv.read",
            "construction.workforce.cv.import",
            "construction.workforce.profile.edit",
            "construction.workforce.profile.verify",
            "construction.execution.read",
            "construction.execution.update",
            "construction.project_documents.read",
            "construction.project_documents.manage",
            "construction.contracts.read",
            "construction.contracts.manage",
            "construction.contracts.activate",
        }
    )



def test_con_002_permission_guard_rejects_codes_outside_registry():
    from api.routes.construction import (
        require_construction_permission,
    )

    allowed = require_construction_permission(
        "construction.projects.read"
    )

    assert (
        allowed.construction_permission_code
        == "construction.projects.read"
    )

    with pytest.raises(
        ValueError,
        match="registered",
    ):
        require_construction_permission(
            "construction.projects.delete"
        )

    with pytest.raises(
        ValueError,
        match="registered",
    ):
        require_construction_permission(
            "construction.unknown.manage"
        )




def test_con_002_access_requires_resolved_permission_and_allowed_scope():
    from dataclasses import replace

    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
        ConstructionScopeDeniedError,
        require_construction_access,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    permission = "construction.projects.read"

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )

    empty_access = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=(),
        permissions=(),
        grants=(),
    )

    with pytest.raises(
        ConstructionPermissionDeniedError
    ):
        require_construction_access(
            actor=actor,
            access_context=empty_access,
            permission_code=permission,
        )

    permission_only = replace(
        empty_access,
        roles=("OWNER",),
        permissions=(permission,),
    )

    with pytest.raises(
        ConstructionScopeDeniedError
    ):
        require_construction_access(
            actor=actor,
            access_context=permission_only,
            permission_code=permission,
        )

    tenant_access = replace(
        permission_only,
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    assert (
        require_construction_access(
            actor=actor,
            access_context=tenant_access,
            permission_code=permission,
        )
        is None
    )



def test_con_002_access_matches_tenant_project_and_document_scopes():
    from dataclasses import replace

    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionScopeDeniedError,
        require_construction_access,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    document_id = uuid4()
    permission = (
        "construction.project_documents.read"
    )

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )

    base_access = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(permission,),
        grants=(),
    )

    tenant_access = replace(
        base_access,
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    assert (
        require_construction_access(
            actor=actor,
            access_context=tenant_access,
            permission_code=permission,
        )
        is None
    )

    project_access = replace(
        base_access,
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    assert (
        require_construction_access(
            actor=actor,
            access_context=project_access,
            permission_code=permission,
            project_id=project_id,
        )
        is None
    )
    assert (
        require_construction_access(
            actor=actor,
            access_context=project_access,
            permission_code=permission,
            project_id=project_id,
            document_id=document_id,
        )
        is None
    )

    document_access = replace(
        base_access,
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="document",
                scope_id=document_id,
            ),
        ),
    )
    assert (
        require_construction_access(
            actor=actor,
            access_context=document_access,
            permission_code=permission,
            project_id=project_id,
            document_id=document_id,
        )
        is None
    )

    with pytest.raises(
        ConstructionScopeDeniedError
    ):
        require_construction_access(
            actor=actor,
            access_context=project_access,
            permission_code=permission,
            project_id=uuid4(),
            document_id=document_id,
        )

    with pytest.raises(
        ConstructionScopeDeniedError
    ):
        require_construction_access(
            actor=actor,
            access_context=document_access,
            permission_code=permission,
            project_id=project_id,
            document_id=uuid4(),
        )



def test_con_002_client_is_not_a_construction_role():
    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
        require_construction_access,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    permission = "construction.projects.read"

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )

    invalid_access = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("CLIENT",),
        permissions=(permission,),
        grants=(
            ConstructionGrantContext(
                role="CLIENT",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    with pytest.raises(
        ConstructionPermissionDeniedError
    ):
        require_construction_access(
            actor=actor,
            access_context=invalid_access,
            permission_code=permission,
        )



def test_con_002_access_management_requires_explicit_resolved_permission():
    from dataclasses import replace

    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
        require_construction_access_manager,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )

    team_manager = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=(
            "construction.team.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="ADMIN",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    with pytest.raises(
        ConstructionPermissionDeniedError
    ):
        require_construction_access_manager(
            actor=actor,
            access_context=team_manager,
        )

    access_manager = replace(
        team_manager,
        permissions=(
            "construction.access.manage",
        ),
    )

    assert (
        require_construction_access_manager(
            actor=actor,
            access_context=access_manager,
        )
        is None
    )



def test_con_002_access_manager_cannot_delegate_beyond_own_scope():
    from dataclasses import replace

    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionScopeDeniedError,
        require_delegable_construction_scope,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    document_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )

    project_admin = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=(
            "construction.access.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="ADMIN",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )

    assert (
        require_delegable_construction_scope(
            actor=actor,
            access_context=project_admin,
            scope_type="project",
            scope_id=project_id,
        )
        is None
    )

    assert (
        require_delegable_construction_scope(
            actor=actor,
            access_context=project_admin,
            scope_type="document",
            scope_id=document_id,
            project_id=project_id,
        )
        is None
    )

    with pytest.raises(
        ConstructionScopeDeniedError
    ):
        require_delegable_construction_scope(
            actor=actor,
            access_context=project_admin,
            scope_type="project",
            scope_id=uuid4(),
        )

    with pytest.raises(
        ConstructionScopeDeniedError
    ):
        require_delegable_construction_scope(
            actor=actor,
            access_context=project_admin,
            scope_type="tenant",
            scope_id=tenant_id,
        )

    tenant_admin = replace(
        project_admin,
        grants=(
            ConstructionGrantContext(
                role="ADMIN",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    assert (
        require_delegable_construction_scope(
            actor=actor,
            access_context=tenant_admin,
            scope_type="project",
            scope_id=uuid4(),
        )
        is None
    )


def test_con_002_access_grant_model_matches_confirmed_contract():
    from sqlalchemy import CheckConstraint

    from database.models import (
        ConstructionAccessGrant,
    )

    table = ConstructionAccessGrant.__table__

    assert table.name == "construction_access_grants"

    expected_columns = {
        "id",
        "tenant_id",
        "user_id",
        "role",
        "scope_type",
        "scope_id",
        "status",
        "expires_at",
        "granted_by_user_id",
        "granted_at",
        "revoked_by_user_id",
        "revoked_at",
    }
    assert expected_columns == {
        column.name
        for column in table.columns
    }

    required_columns = {
        "id",
        "tenant_id",
        "user_id",
        "role",
        "scope_type",
        "scope_id",
        "status",
        "granted_by_user_id",
        "granted_at",
    }
    assert all(
        not table.c[name].nullable
        for name in required_columns
    )

    assert table.c.expires_at.nullable
    assert table.c.revoked_by_user_id.nullable
    assert table.c.revoked_at.nullable

    foreign_keys = {
        (
            foreign_key.parent.name,
            foreign_key.target_fullname,
        )
        for foreign_key in table.foreign_keys
    }
    assert {
        ("tenant_id", "tenants.id"),
        ("user_id", "users.id"),
        ("granted_by_user_id", "users.id"),
        ("revoked_by_user_id", "users.id"),
    } <= foreign_keys

    checks = {
        constraint.name: " ".join(
            str(constraint.sqltext).split()
        ).upper()
        for constraint in table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    role_check = checks[
        "ck_construction_access_grants_role"
    ]
    for role in (
        "OWNER",
        "ADMIN",
        "SURVEYOR",
        "ESTIMATOR",
        "PROJECT_MANAGER",
        "EXECUTOR",
        "OBSERVER",
    ):
        assert f"'{role}'" in role_check

    scope_check = checks[
        "ck_construction_access_grants_scope_type"
    ]
    for scope_type in (
        "tenant",
        "project",
        "document",
    ):
        assert f"'{scope_type.upper()}'" in scope_check

    status_check = checks[
        "ck_construction_access_grants_status"
    ]
    assert "'ACTIVE'" in status_check
    assert "'REVOKED'" in status_check
    assert "'EXPIRED'" not in status_check

    revocation_check = checks[
        "ck_construction_access_grants_revocation"
    ]
    assert "REVOKED_AT" in revocation_check
    assert "REVOKED_BY_USER_ID" in revocation_check

    expiration_check = checks[
        "ck_construction_access_grants_expiration"
    ]
    assert "EXPIRES_AT" in expiration_check
    assert "GRANTED_AT" in expiration_check

    assert "permission_code" not in table.columns



def test_con_002_role_permissions_use_fixed_policy_without_database_table():
    import database.models as models

    assert (
        "construction_role_permissions"
        not in models.Base.metadata.tables
    )
    assert not hasattr(
        models,
        "ConstructionRolePermission",
    )


@pytest.mark.asyncio
async def test_con_002_repository_lists_only_active_nonexpired_user_grants_in_tenant():
    from datetime import UTC, datetime

    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionAccessRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    as_of = datetime(
        2026,
        9,
        16,
        12,
        0,
        tzinfo=UTC,
    )
    expected = [
        object(),
        object(),
    ]

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.commits = 0

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

        async def commit(self):
            self.commits += 1

    session = FakeSession()
    repository = ConstructionAccessRepository(
        session
    )

    result = await repository.list_active_grants(
        tenant_id=tenant_id,
        user_id=user_id,
        as_of=as_of,
    )

    assert result == expected
    assert session.commits == 0
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized_sql = " ".join(
        sql.lower().split()
    )

    assert (
        "from construction_access_grants"
        in normalized_sql
    )
    assert (
        "construction_access_grants.tenant_id ="
        in normalized_sql
    )
    assert str(tenant_id) in normalized_sql
    assert (
        "construction_access_grants.user_id ="
        in normalized_sql
    )
    assert str(user_id) in normalized_sql
    assert (
        "construction_access_grants.status = 'active'"
        in normalized_sql
    )
    assert (
        "construction_access_grants.expires_at is null"
        in normalized_sql
    )
    assert (
        "construction_access_grants.expires_at >"
        in normalized_sql
    )



@pytest.mark.asyncio
async def test_con_002_repository_creates_access_grant_without_internal_commit():
    from datetime import UTC, datetime

    from database.models import (
        ConstructionAccessGrant,
    )
    from database.repositories.construction import (
        ConstructionAccessRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    granted_by_user_id = uuid4()

    granted_at = datetime(
        2026,
        9,
        16,
        13,
        0,
        tzinfo=UTC,
    )
    expires_at = datetime(
        2026,
        10,
        16,
        13,
        0,
        tzinfo=UTC,
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushes = 0
            self.commits = 0

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            self.flushes += 1

        async def commit(self):
            self.commits += 1

    session = FakeSession()
    repository = ConstructionAccessRepository(
        session
    )

    result = await repository.create_grant(
        tenant_id=tenant_id,
        user_id=user_id,
        role="PROJECT_MANAGER",
        scope_type="project",
        scope_id=project_id,
        expires_at=expires_at,
        granted_by_user_id=granted_by_user_id,
        granted_at=granted_at,
    )

    assert isinstance(
        result,
        ConstructionAccessGrant,
    )
    assert session.added == [result]
    assert session.flushes == 1
    assert session.commits == 0

    assert result.tenant_id == tenant_id
    assert result.user_id == user_id
    assert result.role == "PROJECT_MANAGER"
    assert result.scope_type == "project"
    assert result.scope_id == project_id
    assert result.status == "active"
    assert result.expires_at == expires_at
    assert (
        result.granted_by_user_id
        == granted_by_user_id
    )
    assert result.granted_at == granted_at
    assert result.revoked_by_user_id is None
    assert result.revoked_at is None



@pytest.mark.asyncio
async def test_con_002_repository_locks_tenant_access_grant_before_revoke():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionAccessRepository,
    )

    tenant_id = uuid4()
    grant_id = uuid4()
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
    repository = ConstructionAccessRepository(
        session
    )

    result = await repository.get_grant_for_update(
        tenant_id=tenant_id,
        grant_id=grant_id,
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
    normalized_sql = " ".join(
        sql.lower().split()
    )

    assert (
        "from construction_access_grants"
        in normalized_sql
    )
    assert (
        "construction_access_grants.tenant_id ="
        in normalized_sql
    )
    assert str(tenant_id) in normalized_sql
    assert (
        "construction_access_grants.id ="
        in normalized_sql
    )
    assert str(grant_id) in normalized_sql
    assert "for update" in normalized_sql



@pytest.mark.asyncio
async def test_con_002_repository_revokes_locked_grant_idempotently_without_commit():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from database.repositories.construction import (
        ConstructionAccessRepository,
    )

    revoked_by_user_id = uuid4()
    revoked_at = datetime(
        2026,
        9,
        16,
        14,
        0,
        tzinfo=UTC,
    )

    class FakeSession:
        def __init__(self):
            self.flushes = 0
            self.commits = 0

        async def flush(self):
            self.flushes += 1

        async def commit(self):
            self.commits += 1

    session = FakeSession()
    repository = ConstructionAccessRepository(
        session
    )

    active_grant = SimpleNamespace(
        status="active",
        revoked_by_user_id=None,
        revoked_at=None,
    )

    result = await repository.revoke_grant(
        grant=active_grant,
        revoked_by_user_id=revoked_by_user_id,
        revoked_at=revoked_at,
    )

    assert result is active_grant
    assert active_grant.status == "revoked"
    assert (
        active_grant.revoked_by_user_id
        == revoked_by_user_id
    )
    assert active_grant.revoked_at == revoked_at
    assert session.flushes == 1
    assert session.commits == 0

    original_revoker = uuid4()
    original_revoked_at = datetime(
        2026,
        9,
        15,
        10,
        0,
        tzinfo=UTC,
    )
    revoked_grant = SimpleNamespace(
        status="revoked",
        revoked_by_user_id=original_revoker,
        revoked_at=original_revoked_at,
    )

    repeated_result = await repository.revoke_grant(
        grant=revoked_grant,
        revoked_by_user_id=revoked_by_user_id,
        revoked_at=revoked_at,
    )

    assert repeated_result is revoked_grant
    assert (
        revoked_grant.revoked_by_user_id
        == original_revoker
    )
    assert (
        revoked_grant.revoked_at
        == original_revoked_at
    )
    assert session.flushes == 1
    assert session.commits == 0



@pytest.mark.asyncio
async def test_con_002_repository_verifies_active_platform_user_membership():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionAccessRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    class FakeResult:
        def scalar_one_or_none(self):
            return user_id

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = ConstructionAccessRepository(
        session
    )

    result = (
        await repository.has_active_platform_membership(
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )

    assert result is True
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    normalized_sql = " ".join(
        sql.lower().split()
    )

    assert "from users" in normalized_sql
    assert "users.id =" in normalized_sql
    assert str(user_id) in normalized_sql
    assert "users.status = 'active'" in normalized_sql

    assert "users.tenant_id =" in normalized_sql
    assert "user_roles" in normalized_sql
    assert "user_roles.user_id = users.id" in normalized_sql
    assert "user_roles.tenant_id =" in normalized_sql
    assert "user_roles.status = 'active'" in normalized_sql
    assert str(tenant_id) in normalized_sql



def test_con_002_authorization_uses_separate_construction_access_context():
    from dataclasses import replace

    from services.api_identity import (
        ApiActorContext,
        ApiRoleScopeContext,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
        require_construction_access,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    permission = "construction.projects.read"

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )

    construction_access = (
        ConstructionAccessContext(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=("ESTIMATOR",),
            permissions=(permission,),
            grants=(
                ConstructionGrantContext(
                    role="ESTIMATOR",
                    scope_type="project",
                    scope_id=project_id,
                ),
            ),
        )
    )

    assert (
        require_construction_access(
            actor=actor,
            access_context=construction_access,
            permission_code=permission,
            project_id=project_id,
        )
        is None
    )

    platform_only_actor = replace(
        actor,
        roles=("admin", "estimator"),
        permissions=(permission,),
        role_scopes=(
            ApiRoleScopeContext(
                role="estimator",
                scope_type="project",
                scope_id=project_id,
                scope_code=None,
            ),
        ),
    )
    empty_construction_access = (
        ConstructionAccessContext(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=(),
            permissions=(),
            grants=(),
        )
    )

    with pytest.raises(
        ConstructionPermissionDeniedError
    ):
        require_construction_access(
            actor=platform_only_actor,
            access_context=(
                empty_construction_access
            ),
            permission_code=permission,
            project_id=project_id,
        )



def test_con_002_access_management_uses_resolved_construction_context():
    from dataclasses import replace

    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
        require_construction_access_manager,
    )

    tenant_id = uuid4()
    user_id = uuid4()

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )

    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=(
            "construction.access.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="ADMIN",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    assert (
        require_construction_access_manager(
            actor=actor,
            access_context=access_context,
        )
        is None
    )

    platform_owner = replace(
        actor,
        active_role="owner",
        roles=("owner",),
        permissions=(
            "construction.access.manage",
        ),
    )
    empty_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=(),
        permissions=(),
        grants=(),
    )

    with pytest.raises(
        ConstructionPermissionDeniedError
    ):
        require_construction_access_manager(
            actor=platform_owner,
            access_context=empty_context,
        )



@pytest.mark.asyncio
async def test_con_002_grant_service_issues_scoped_access_and_audits_atomically():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_grants import (
        ConstructionAccessGrantService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    target_user_id = uuid4()
    project_id = uuid4()
    grant_id = uuid4()

    now = datetime(
        2026,
        9,
        16,
        15,
        0,
        tzinfo=UTC,
    )
    expires_at = datetime(
        2026,
        10,
        16,
        15,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )
    access_context = ConstructionAccessContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=(
            "construction.access.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="ADMIN",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    grant = SimpleNamespace(
        id=grant_id,
        tenant_id=tenant_id,
        user_id=target_user_id,
        role="PROJECT_MANAGER",
        scope_type="project",
        scope_id=project_id,
        status="active",
        expires_at=expires_at,
        granted_by_user_id=actor_user_id,
        granted_at=now,
    )

    class FakeProjectRepository:
        async def get_active_project(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            calls.append(("membership", kwargs))
            return True

        async def create_grant(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return grant

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    service = ConstructionAccessGrantService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
        now_provider=lambda: now,
    )

    result = await service.grant_access(
        actor=actor,
        access_context=access_context,
        target_user_id=target_user_id,
        role="PROJECT_MANAGER",
        scope_type="project",
        scope_id=project_id,
        project_id=project_id,
        expires_at=expires_at,
        trace_id="trace-con-002-grant",
    )

    assert result is grant
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "membership",
            {
                "tenant_id": tenant_id,
                "user_id": target_user_id,
            },
        ),
        (
            "create",
            {
                "tenant_id": tenant_id,
                "user_id": target_user_id,
                "role": "PROJECT_MANAGER",
                "scope_type": "project",
                "scope_id": project_id,
                "expires_at": expires_at,
                "granted_by_user_id": (
                    actor_user_id
                ),
                "granted_at": now,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_access_grant_created"
                ),
                "tenant_id": tenant_id,
                "user_id": actor_user_id,
                "entity_type": (
                    "construction_access_grant"
                ),
                "entity_id": grant_id,
                "payload": {
                    "operation": "create",
                    "changes": [],
                    "reason": None,
                    "target_user_id": str(
                        target_user_id
                    ),
                    "role": "PROJECT_MANAGER",
                    "scope_type": "project",
                    "scope_id": str(project_id),
                    "expires_at": (
                        expires_at.isoformat()
                    ),
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-002-grant"
                ),
            },
        ),
        ("commit", {}),
    ]



@pytest.mark.asyncio
async def test_con_002_grant_service_revokes_locked_access_and_audits_atomically():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_grants import (
        ConstructionAccessGrantService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    target_user_id = uuid4()
    project_id = uuid4()
    grant_id = uuid4()

    revoked_at = datetime(
        2026,
        9,
        16,
        16,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )
    access_context = ConstructionAccessContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=(
            "construction.access.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="ADMIN",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    grant = SimpleNamespace(
        id=grant_id,
        tenant_id=tenant_id,
        user_id=target_user_id,
        role="PROJECT_MANAGER",
        scope_type="project",
        scope_id=project_id,
        status="active",
        revoked_by_user_id=None,
        revoked_at=None,
    )

    class FakeRepository:
        async def get_grant_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return grant

        async def revoke_grant(
            self,
            **kwargs,
        ):
            calls.append(("revoke", kwargs))
            grant.status = "revoked"
            grant.revoked_by_user_id = (
                kwargs["revoked_by_user_id"]
            )
            grant.revoked_at = kwargs["revoked_at"]
            return grant

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    service = ConstructionAccessGrantService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEventRepository(),
        now_provider=lambda: revoked_at,
    )

    result = await service.revoke_access(
        actor=actor,
        access_context=access_context,
        grant_id=grant_id,
        trace_id="trace-con-002-revoke",
    )

    assert result is grant
    assert calls == [
        (
            "lock",
            {
                "tenant_id": tenant_id,
                "grant_id": grant_id,
            },
        ),
        (
            "revoke",
            {
                "grant": grant,
                "revoked_by_user_id": (
                    actor_user_id
                ),
                "revoked_at": revoked_at,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_access_grant_revoked"
                ),
                "tenant_id": tenant_id,
                "user_id": actor_user_id,
                "entity_type": (
                    "construction_access_grant"
                ),
                "entity_id": grant_id,
                "payload": {
                    "operation": "revoke",
                    "changes": [
                        {
                            "field": "status",
                            "old_value": "active",
                            "new_value": "revoked",
                        },
                        {
                            "field": (
                                "revoked_by_user_id"
                            ),
                            "old_value": None,
                            "new_value": str(
                                actor_user_id
                            ),
                        },
                        {
                            "field": "revoked_at",
                            "old_value": None,
                            "new_value": (
                                revoked_at.isoformat()
                            ),
                        },
                    ],
                    "reason": None,
                    "target_user_id": str(
                        target_user_id
                    ),
                    "role": "PROJECT_MANAGER",
                    "scope_type": "project",
                    "scope_id": str(project_id),
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-002-revoke"
                ),
            },
        ),
        ("commit", {}),
    ]



@pytest.mark.asyncio
async def test_con_002_access_context_resolver_uses_active_grants_and_role_policy():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from services.api_identity import (
        ApiActorContext,
    )
    from services.construction_grants import (
        ConstructionAccessContextResolver,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    document_id = uuid4()
    now = datetime(
        2026,
        9,
        16,
        17,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="ru",
        timezone="Europe/Lisbon",
        status="active",
        permissions=(),
        role_scopes=(),
    )

    grants = [
        SimpleNamespace(
            role="PROJECT_MANAGER",
            scope_type="project",
            scope_id=project_id,
        ),
        SimpleNamespace(
            role="ESTIMATOR",
            scope_type="document",
            scope_id=document_id,
        ),
    ]

    class FakeRepository:
        async def list_active_grants(
            self,
            **kwargs,
        ):
            calls.append(("grants", kwargs))
            return grants

    class FakeRolePolicy:
        async def list_permissions(
            self,
            *,
            roles,
        ):
            calls.append(
                (
                    "permissions",
                    {
                        "roles": roles,
                    },
                )
            )
            return (
                "construction.estimates.read",
                "construction.projects.read",
            )

    resolver = ConstructionAccessContextResolver(
        repository=FakeRepository(),
        role_policy=FakeRolePolicy(),
        now_provider=lambda: now,
    )

    result = await resolver.resolve(
        actor=actor,
    )

    assert result == ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=(
            "ESTIMATOR",
            "PROJECT_MANAGER",
        ),
        permissions=(
            "construction.estimates.read",
            "construction.projects.read",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
            ConstructionGrantContext(
                role="ESTIMATOR",
                scope_type="document",
                scope_id=document_id,
            ),
        ),
    )

    assert calls == [
        (
            "grants",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "as_of": now,
            },
        ),
        (
            "permissions",
            {
                "roles": (
                    "ESTIMATOR",
                    "PROJECT_MANAGER",
                ),
            },
        ),
    ]

def test_con_002_fixed_role_permission_policy_matches_confirmed_matrix():
    from services.construction_permissions import (
        CONSTRUCTION_PERMISSION_CODES,
        CONSTRUCTION_ROLE_PERMISSIONS,
        CONSTRUCTION_ROLES,
    )

    expected = {
        "OWNER": CONSTRUCTION_PERMISSION_CODES,
        "ADMIN": (
            CONSTRUCTION_PERMISSION_CODES
            - {
                "construction.contracts.activate",
            }
        ),
        "SURVEYOR": frozenset(
            {
                "construction.projects.read",
                "construction.measurements.read",
                "construction.measurements.create",
                "construction.measurements.edit",
                "construction.measurements.submit",
                "construction.materials.read",
                "construction.documents.generate",
                "construction.exports.internal",
                "construction.logistics.read",
                "construction.planning.read",
                "construction.execution.read",
                "construction.project_documents.read",
            }
        ),
        "ESTIMATOR": frozenset(
            {
                "construction.projects.read",
                "construction.measurements.read",
                "construction.estimates.read",
                "construction.estimates.create",
                "construction.estimates.edit",
                "construction.estimates.submit",
                "construction.pricing.read",
                "construction.costs.read",
                "construction.costs.edit",
                "construction.materials.read",
                "construction.materials.edit",
                "construction.tax.read",
                "construction.documents.generate",
                "construction.exports.internal",
                "construction.exports.client",
                "construction.exports.procurement",
                "construction.logistics.read",
                "construction.planning.read",
                "construction.workforce.read",
                "construction.project_documents.read",
                "construction.contracts.read",
            }
        ),
        "PROJECT_MANAGER": frozenset(
            {
                "construction.projects.read",
                "construction.projects.create",
                "construction.projects.edit",
                "construction.measurements.read",
                "construction.measurements.create",
                "construction.measurements.edit",
                "construction.measurements.submit",
                "construction.measurements.verify",
                "construction.estimates.read",
                "construction.estimates.create",
                "construction.estimates.edit",
                "construction.estimates.submit",
                "construction.estimates.approve",
                "construction.pricing.read",
                "construction.costs.read",
                "construction.costs.edit",
                "construction.materials.read",
                "construction.materials.edit",
                "construction.tax.read",
                "construction.documents.generate",
                "construction.exports.internal",
                "construction.exports.client",
                "construction.exports.procurement",
                "construction.team.manage",
                "construction.logistics.read",
                "construction.logistics.edit",
                "construction.planning.read",
                "construction.planning.edit",
                "construction.planning.approve",
                "construction.workforce.read",
                "construction.workforce.assign",
                "construction.workforce.crews.read",
                "construction.workforce.crews.manage",
                "construction.workforce.cv.read",
                "construction.execution.read",
                "construction.execution.update",
                "construction.project_documents.read",
                "construction.project_documents.manage",
                "construction.contracts.read",
                "construction.contracts.manage",
            }
        ),
        "EXECUTOR": frozenset(
            {
                "construction.projects.read",
                "construction.measurements.read",
                "construction.materials.read",
                "construction.logistics.read",
                "construction.planning.read",
                "construction.workforce.read",
                "construction.workforce.crews.read",
                "construction.execution.read",
                "construction.execution.update",
                "construction.project_documents.read",
            }
        ),
        "OBSERVER": frozenset(
            {
                "construction.projects.read",
                "construction.measurements.read",
                "construction.estimates.read",
                "construction.pricing.read",
                "construction.costs.read",
                "construction.materials.read",
                "construction.tax.read",
                "construction.logistics.read",
                "construction.planning.read",
                "construction.workforce.read",
                "construction.workforce.crews.read",
                "construction.execution.read",
                "construction.project_documents.read",
                "construction.contracts.read",
            }
        ),
    }

    assert frozenset(
        CONSTRUCTION_ROLE_PERMISSIONS
    ) == CONSTRUCTION_ROLES
    assert CONSTRUCTION_ROLE_PERMISSIONS == expected

    assert {
        role: len(permissions)
        for role, permissions
        in CONSTRUCTION_ROLE_PERMISSIONS.items()
    } == {
        "OWNER": 47,
        "ADMIN": 46,
        "SURVEYOR": 12,
        "ESTIMATOR": 21,
        "PROJECT_MANAGER": 40,
        "EXECUTOR": 10,
        "OBSERVER": 14,
    }

    assert all(
        permissions <= CONSTRUCTION_PERMISSION_CODES
        for permissions
        in CONSTRUCTION_ROLE_PERMISSIONS.values()
    )

@pytest.mark.asyncio
async def test_con_002_fixed_role_policy_resolves_permission_union_and_fails_closed():
    from services.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS,
        ConstructionPermissionDeniedError,
        ConstructionRolePermissionPolicy,
    )

    policy = ConstructionRolePermissionPolicy()

    permissions = await policy.list_permissions(
        roles=(
            "SURVEYOR",
            "OBSERVER",
        ),
    )

    assert permissions == tuple(
        sorted(
            CONSTRUCTION_ROLE_PERMISSIONS[
                "SURVEYOR"
            ]
            | CONSTRUCTION_ROLE_PERMISSIONS[
                "OBSERVER"
            ]
        )
    )

    assert (
        await policy.list_permissions(
            roles=(),
        )
        == ()
    )

    with pytest.raises(
        ConstructionPermissionDeniedError
    ):
        await policy.list_permissions(
            roles=(
                "UNKNOWN_ROLE",
            ),
        )

@pytest.mark.asyncio
async def test_con_002_access_context_resolver_uses_fixed_role_policy_by_default():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_grants import (
        ConstructionAccessContextResolver,
    )
    from services.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )

    class FakeRepository:
        async def list_active_grants(
            self,
            **kwargs,
        ):
            return [
                SimpleNamespace(
                    role="PROJECT_MANAGER",
                    scope_type="project",
                    scope_id=project_id,
                ),
                SimpleNamespace(
                    role="OBSERVER",
                    scope_type="project",
                    scope_id=project_id,
                ),
            ]

    resolver = ConstructionAccessContextResolver(
        repository=FakeRepository(),
    )

    result = await resolver.resolve(
        actor=actor,
    )

    assert result.roles == (
        "OBSERVER",
        "PROJECT_MANAGER",
    )
    assert result.permissions == tuple(
        sorted(
            CONSTRUCTION_ROLE_PERMISSIONS[
                "OBSERVER"
            ]
            | CONSTRUCTION_ROLE_PERMISSIONS[
                "PROJECT_MANAGER"
            ]
        )
    )

def test_con_002_access_grants_prevent_overlapping_active_scope_periods():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from database.models import (
        ConstructionAccessGrant,
    )

    table = ConstructionAccessGrant.__table__

    exclusion_constraints = [
        constraint
        for constraint in table.constraints
        if constraint.__class__.__name__
        == "ExcludeConstraint"
    ]

    assert len(exclusion_constraints) == 1
    assert exclusion_constraints[0].name == (
        "ex_construction_access_grants_active_period"
    )

    ddl = " ".join(
        str(
            CreateTable(table).compile(
                dialect=postgresql.dialect()
            )
        ).split()
    ).upper()

    assert "EXCLUDE USING GIST" in ddl
    assert "TENANT_ID WITH =" in ddl
    assert "USER_ID WITH =" in ddl
    assert "ROLE WITH =" in ddl
    assert "SCOPE_TYPE WITH =" in ddl
    assert "SCOPE_ID WITH =" in ddl
    assert "TSTZRANGE(" in ddl
    assert "GRANTED_AT" in ddl
    assert "EXPIRES_AT" in ddl
    assert "WITH &&" in ddl
    assert "WHERE (STATUS = 'ACTIVE')" in ddl

def test_con_003_client_model_matches_confirmed_release_contract():
    from sqlalchemy import CheckConstraint

    from database.models import (
        ConstructionClient,
    )

    table = ConstructionClient.__table__

    assert table.name == "construction_clients"
    assert {
        column.name
        for column in table.columns
    } == {
        "id",
        "tenant_id",
        "display_name",
        "client_type",
        "phone",
        "email",
        "notes",
        "created_at",
        "updated_at",
        "deleted_at",
    }

    assert table.c.id.primary_key
    assert not table.c.id.nullable
    assert not table.c.tenant_id.nullable
    assert not table.c.display_name.nullable
    assert not table.c.client_type.nullable
    assert table.c.phone.nullable
    assert table.c.email.nullable
    assert table.c.notes.nullable
    assert not table.c.created_at.nullable
    assert not table.c.updated_at.nullable
    assert table.c.deleted_at.nullable

    foreign_keys = {
        (
            foreign_key.parent.name,
            foreign_key.target_fullname,
        )
        for foreign_key in table.foreign_keys
    }
    assert (
        "tenant_id",
        "tenants.id",
    ) in foreign_keys

    checks = {
        constraint.name: " ".join(
            str(constraint.sqltext).split()
        ).upper()
        for constraint in table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    client_type_check = checks[
        "ck_construction_clients_client_type"
    ]
    assert "'PERSON'" in client_type_check
    assert "'COMPANY'" in client_type_check

    assert "linked_user_id" not in table.columns
    assert "status" not in table.columns

@pytest.mark.asyncio
async def test_con_003_repository_creates_tenant_client_without_internal_commit():
    from uuid import uuid4

    from database.models import (
        ConstructionClient,
    )
    from database.repositories.construction import (
        ConstructionClientRepository,
    )

    tenant_id = uuid4()
    calls = []

    class FakeSession:
        def add(self, value):
            calls.append(
                (
                    "add",
                    value,
                )
            )

        async def flush(self):
            calls.append(
                (
                    "flush",
                    {},
                )
            )

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionClientRepository(
        FakeSession()
    )

    result = await repository.create_client(
        tenant_id=tenant_id,
        display_name="Joao Silva",
        client_type="person",
        phone="+351910000000",
        email="joao@example.com",
        notes="Primary client",
    )

    assert isinstance(
        result,
        ConstructionClient,
    )
    assert result.tenant_id == tenant_id
    assert result.display_name == "Joao Silva"
    assert result.client_type == "person"
    assert result.phone == "+351910000000"
    assert result.email == "joao@example.com"
    assert result.notes == "Primary client"
    assert result.deleted_at is None

    assert calls == [
        (
            "add",
            result,
        ),
        (
            "flush",
            {},
        ),
    ]

@pytest.mark.asyncio
async def test_con_003_client_service_creates_client_in_verified_actor_tenant():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_clients import (
        ConstructionClientService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    client_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    expected = SimpleNamespace(
        id=client_id,
        tenant_id=tenant_id,
        display_name="Joao Silva",
        client_type="person",
        phone="+351910000000",
        email="joao@example.com",
        notes=None,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def create_client(
            self,
            **kwargs,
        ):
            calls.append(
                (
                    "create",
                    kwargs,
                )
            )
            return expected

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionClientService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.create_client(
        actor=actor,
        access_context=access_context,
        display_name="Joao Silva",
        client_type="person",
        phone="+351910000000",
        email="joao@example.com",
        notes=None,
        trace_id="trace-con-003-create",
    )

    assert result is expected
    assert calls == [
        (
            "create",
            {
                "tenant_id": tenant_id,
                "display_name": "Joao Silva",
                "client_type": "person",
                "phone": "+351910000000",
                "email": "joao@example.com",
                "notes": None,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_client_created"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_client"
                ),
                "entity_id": client_id,
                "payload": {
                    "operation": "create",
                    "changes": [],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-003-create"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]

@pytest.mark.asyncio
async def test_con_003_repository_gets_only_active_client_in_tenant_scope():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionClientRepository,
    )

    tenant_id = uuid4()
    client_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(
            self,
            statement,
        ):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionClientRepository(
        FakeSession()
    )

    result = await repository.get_active_client(
        tenant_id=tenant_id,
        client_id=client_id,
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(client_id).upper() in sql
    assert "CONSTRUCTION_CLIENTS.TENANT_ID" in sql
    assert "CONSTRUCTION_CLIENTS.ID" in sql
    assert "CONSTRUCTION_CLIENTS.DELETED_AT IS NULL" in sql

@pytest.mark.asyncio
async def test_con_003_repository_locks_active_tenant_client_before_change():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionClientRepository,
    )

    tenant_id = uuid4()
    client_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(
            self,
            statement,
        ):
            statements.append(statement)
            return FakeResult()

    repository = ConstructionClientRepository(
        FakeSession()
    )

    result = (
        await repository
        .get_active_client_for_update(
            tenant_id=tenant_id,
            client_id=client_id,
        )
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(client_id).upper() in sql
    assert "CONSTRUCTION_CLIENTS.TENANT_ID" in sql
    assert "CONSTRUCTION_CLIENTS.ID" in sql
    assert "CONSTRUCTION_CLIENTS.DELETED_AT IS NULL" in sql
    assert "FOR UPDATE" in sql

@pytest.mark.asyncio
async def test_con_003_repository_soft_deletes_locked_client_without_internal_commit():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from database.repositories.construction import (
        ConstructionClientRepository,
    )

    deleted_at = datetime(
        2026,
        9,
        17,
        12,
        0,
        tzinfo=UTC,
    )
    client = SimpleNamespace(
        deleted_at=None,
    )
    calls = []

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionClientRepository(
        FakeSession()
    )

    result = await repository.soft_delete_client(
        client=client,
        deleted_at=deleted_at,
    )

    assert result is client
    assert client.deleted_at == deleted_at
    assert calls == [
        (
            "flush",
            {},
        ),
    ]

@pytest.mark.asyncio
async def test_con_003_client_service_archives_locked_client_in_verified_tenant():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_clients import (
        ConstructionClientService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    client_id = uuid4()
    deleted_at = datetime(
        2026,
        9,
        17,
        13,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    client = SimpleNamespace(
        id=client_id,
        tenant_id=tenant_id,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_client_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return client

        async def soft_delete_client(
            self,
            **kwargs,
        ):
            calls.append(("delete", kwargs))
            kwargs["client"].deleted_at = (
                kwargs["deleted_at"]
            )
            return kwargs["client"]

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionClientService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEventRepository(),
        now_provider=lambda: deleted_at,
    )

    result = await service.archive_client(
        actor=actor,
        access_context=access_context,
        client_id=client_id,
        trace_id="trace-con-003-archive",
    )

    assert result is client
    assert result.deleted_at == deleted_at
    assert calls == [
        (
            "get",
            {
                "tenant_id": tenant_id,
                "client_id": client_id,
            },
        ),
        (
            "delete",
            {
                "client": client,
                "deleted_at": deleted_at,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_client_archived"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_client"
                ),
                "entity_id": client_id,
                "payload": {
                    "operation": "archive",
                    "changes": [
                        {
                            "field": "deleted_at",
                            "old_value": None,
                            "new_value": (
                                deleted_at.isoformat()
                            ),
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-003-archive"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]

def test_con_003_client_has_tenant_scoped_identity_for_project_relations():
    from sqlalchemy import UniqueConstraint

    from database.models import (
        ConstructionClient,
    )

    table = ConstructionClient.__table__

    unique_constraints = {
        constraint.name: tuple(
            column.name
            for column in constraint.columns
        )
        for constraint in table.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert unique_constraints[
        "uq_construction_clients_tenant_id_id"
    ] == (
        "tenant_id",
        "id",
    )

def test_con_004_project_model_matches_release_and_address_contract():
    from sqlalchemy import (
        CheckConstraint,
        Numeric,
    )

    from database.models import (
        ConstructionProject,
    )

    table = ConstructionProject.__table__

    assert table.name == "construction_projects"
    assert {
        column.name
        for column in table.columns
    } == {
        "id",
        "tenant_id",
        "client_id",
        "name",
        "status",
        "responsible_user_id",
        "comment",
        "address_raw",
        "address_formatted",
        "country_code",
        "region",
        "city",
        "postal_code",
        "latitude",
        "longitude",
        "address_provider",
        "provider_place_id",
        "address_verification_status",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
        "row_version",
    }

    assert not table.c.tenant_id.nullable
    assert table.c.client_id.nullable
    assert not table.c.name.nullable
    assert not table.c.status.nullable
    assert table.c.responsible_user_id.nullable
    assert table.c.comment.nullable
    assert not table.c.address_raw.nullable
    assert table.c.address_formatted.nullable
    assert table.c.country_code.nullable
    assert table.c.region.nullable
    assert table.c.city.nullable
    assert table.c.postal_code.nullable
    assert table.c.latitude.nullable
    assert table.c.longitude.nullable
    assert table.c.address_provider.nullable
    assert table.c.provider_place_id.nullable
    assert not (
        table.c.address_verification_status
        .nullable
    )
    assert not table.c.created_by.nullable
    assert not table.c.created_at.nullable
    assert not table.c.updated_at.nullable
    assert table.c.deleted_at.nullable
    assert not table.c.row_version.nullable

    assert isinstance(
        table.c.latitude.type,
        Numeric,
    )
    assert isinstance(
        table.c.longitude.type,
        Numeric,
    )

    checks = {
        constraint.name: " ".join(
            str(constraint.sqltext).split()
        ).upper()
        for constraint in table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    status_check = checks[
        "ck_construction_projects_status"
    ]
    for status in (
        "DRAFT",
        "PLANNING",
        "ACTIVE",
        "COMPLETED",
        "CANCELLED",
    ):
        assert f"'{status}'" in status_check

    address_status_check = checks[
        "ck_construction_projects_address_status"
    ]
    for status in (
        "PENDING",
        "VERIFIED",
        "FAILED",
    ):
        assert f"'{status}'" in address_status_check

    foreign_key_targets = {
        tuple(
            foreign_key.target_fullname
            for foreign_key
            in constraint.elements
        )
        for constraint
        in table.foreign_key_constraints
    }

    assert (
        "tenants.id",
    ) in foreign_key_targets
    assert (
        "construction_clients.tenant_id",
        "construction_clients.id",
    ) in foreign_key_targets
    assert (
        "users.id",
    ) in foreign_key_targets

    assert "organization_id" not in table.columns
    assert "active" not in table.columns

def test_con_004_project_coordinates_use_decimal_not_float():
    from decimal import Decimal
    from typing import get_args

    from database.models import (
        ConstructionProject,
    )

    for field_name in (
        "latitude",
        "longitude",
    ):
        mapped_annotation = (
            ConstructionProject.__annotations__[
                field_name
            ]
        )
        optional_annotation = get_args(
            mapped_annotation
        )[0]

        assert Decimal in get_args(
            optional_annotation
        )
        assert float not in get_args(
            optional_annotation
        )

@pytest.mark.asyncio
async def test_con_004_repository_creates_draft_project_without_provider_or_internal_commit():
    from uuid import uuid4

    from database.models import (
        ConstructionProject,
    )
    from database.repositories.construction import (
        ConstructionProjectRepository,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    calls = []

    class FakeSession:
        def add(self, value):
            calls.append(("add", value))

        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionProjectRepository(
        FakeSession()
    )

    result = await repository.create_project(
        tenant_id=tenant_id,
        client_id=None,
        name="Casa Joao",
        responsible_user_id=None,
        comment=None,
        address_raw="Rua Exemplo 10, Lisboa",
        created_by=user_id,
    )

    assert isinstance(
        result,
        ConstructionProject,
    )
    assert result.tenant_id == tenant_id
    assert result.client_id is None
    assert result.name == "Casa Joao"
    assert result.status == "draft"
    assert result.responsible_user_id is None
    assert result.comment is None
    assert (
        result.address_raw
        == "Rua Exemplo 10, Lisboa"
    )
    assert result.address_formatted is None
    assert result.country_code is None
    assert result.region is None
    assert result.city is None
    assert result.postal_code is None
    assert result.latitude is None
    assert result.longitude is None
    assert result.address_provider is None
    assert result.provider_place_id is None
    assert (
        result.address_verification_status
        == "pending"
    )
    assert result.created_by == user_id
    assert result.deleted_at is None
    assert result.row_version == 1

    assert calls == [
        (
            "add",
            result,
        ),
        (
            "flush",
            {},
        ),
    ]

@pytest.mark.asyncio
async def test_con_004_repository_updates_locked_project_and_invalidates_verified_address():
    from types import SimpleNamespace
    from uuid import uuid4

    from database.repositories.construction import (
        ConstructionProjectRepository,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    old_client_id = uuid4()
    new_client_id = uuid4()
    old_responsible_id = uuid4()
    new_responsible_id = uuid4()
    calls = []

    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        name="Old project",
        client_id=old_client_id,
        responsible_user_id=(
            old_responsible_id
        ),
        comment="Old comment",
        address_raw="Old address",
        address_formatted="Verified address",
        country_code="PT",
        region="Lisbon",
        city="Lisbon",
        postal_code="1000-001",
        latitude="38.7223",
        longitude="-9.1393",
        address_provider="provider",
        provider_place_id="place-001",
        address_verification_status=(
            "verified"
        ),
        row_version=3,
    )

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            raise AssertionError(
                "REPOSITORY MUST NOT COMMIT"
            )

    repository = ConstructionProjectRepository(
        FakeSession()
    )

    result = await repository.update_project(
        project=project,
        name="Updated project",
        client_id=new_client_id,
        responsible_user_id=(
            new_responsible_id
        ),
        comment="Updated comment",
        address_raw="New address",
        reset_address_verification=True,
    )

    assert result is project
    assert result.name == "Updated project"
    assert result.client_id == new_client_id
    assert (
        result.responsible_user_id
        == new_responsible_id
    )
    assert result.comment == "Updated comment"
    assert result.address_raw == "New address"
    assert result.address_formatted is None
    assert result.country_code is None
    assert result.region is None
    assert result.city is None
    assert result.postal_code is None
    assert result.latitude is None
    assert result.longitude is None
    assert result.address_provider is None
    assert result.provider_place_id is None
    assert (
        result.address_verification_status
        == "pending"
    )
    assert calls == [
        ("flush", {}),
    ]


@pytest.mark.asyncio
async def test_con_004_project_service_creates_draft_from_verified_actor_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    expected = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="draft",
        address_verification_status=(
            "pending"
        ),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def create_project(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return expected

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.create_project(
        actor=actor,
        access_context=access_context,
        name="Casa Joao",
        address_raw="Rua Exemplo 10, Lisboa",
        client_id=None,
        responsible_user_id=None,
        comment=None,
        trace_id="trace-con-004-create",
    )

    assert result is expected
    assert calls == [
        (
            "create",
            {
                "tenant_id": tenant_id,
                "client_id": None,
                "name": "Casa Joao",
                "responsible_user_id": None,
                "comment": None,
                "address_raw": (
                    "Rua Exemplo 10, Lisboa"
                ),
                "created_by": user_id,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_project_created"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project"
                ),
                "entity_id": project_id,
                "payload": {
                    "operation": "create",
                    "changes": [],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-004-create"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]

@pytest.mark.asyncio
async def test_con_004_project_service_validates_optional_client_in_actor_tenant():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    client_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    client = SimpleNamespace(
        id=client_id,
        tenant_id=tenant_id,
        deleted_at=None,
    )
    expected = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        client_id=client_id,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeClientRepository:
        async def get_active_client(
            self,
            **kwargs,
        ):
            calls.append(("client", kwargs))
            return client

    class FakeProjectRepository:
        async def create_project(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return expected

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeProjectRepository(),
        client_repository=(
            FakeClientRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    result = await service.create_project(
        actor=actor,
        access_context=access_context,
        name="Casa Joao",
        address_raw="Rua Exemplo 10",
        client_id=client_id,
        responsible_user_id=None,
        comment=None,
    )

    assert result is expected
    assert calls[0] == (
        "client",
        {
            "tenant_id": tenant_id,
            "client_id": client_id,
        },
    )
    assert calls[1][0] == "create"
    assert (
        calls[1][1]["client_id"]
        == client_id
    )
    assert calls[2][0] == "event"
    assert calls[2][1]["event_type"] == (
        "construction_project_created"
    )
    assert calls[2][1]["entity_id"] == (
        project_id
    )
    assert calls[3] == ("commit", {})

@pytest.mark.asyncio
async def test_con_004_project_service_validates_optional_responsible_platform_membership():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    responsible_user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    expected = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        responsible_user_id=(
            responsible_user_id
        ),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeAccessRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            calls.append(("membership", kwargs))
            return True

    class FakeProjectRepository:
        async def create_project(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return expected

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeProjectRepository(),
        access_repository=(
            FakeAccessRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    result = await service.create_project(
        actor=actor,
        access_context=access_context,
        name="Casa Joao",
        address_raw="Rua Exemplo 10",
        client_id=None,
        responsible_user_id=(
            responsible_user_id
        ),
        comment=None,
    )

    assert result is expected
    assert calls[0] == (
        "membership",
        {
            "tenant_id": tenant_id,
            "user_id": responsible_user_id,
        },
    )
    assert calls[1][0] == "create"
    assert (
        calls[1][1][
            "responsible_user_id"
        ]
        == responsible_user_id
    )
    assert calls[2][0] == "event"
    assert calls[2][1]["event_type"] == (
        "construction_project_created"
    )
    assert calls[2][1]["entity_id"] == (
        project_id
    )
    assert calls[3] == ("commit", {})

@pytest.mark.asyncio
async def test_con_004_project_service_updates_allowed_fields_and_invalidates_address():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    old_client_id = uuid4()
    new_client_id = uuid4()
    old_responsible_id = uuid4()
    new_responsible_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        name="Old project",
        client_id=old_client_id,
        responsible_user_id=(
            old_responsible_id
        ),
        comment="Old comment",
        address_raw="Old address",
        address_formatted="Verified address",
        country_code="PT",
        region="Lisbon",
        city="Lisbon",
        postal_code="1000-001",
        latitude="38.7223",
        longitude="-9.1393",
        address_provider="provider",
        provider_place_id="place-001",
        address_verification_status=(
            "verified"
        ),
        status="planning",
        deleted_at=None,
        row_version=3,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return project

        async def update_project(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            target = kwargs["project"]
            target.name = kwargs["name"]
            target.client_id = kwargs["client_id"]
            target.responsible_user_id = (
                kwargs["responsible_user_id"]
            )
            target.comment = kwargs["comment"]
            target.address_raw = (
                kwargs["address_raw"]
            )
            if kwargs[
                "reset_address_verification"
            ]:
                target.address_formatted = None
                target.country_code = None
                target.region = None
                target.city = None
                target.postal_code = None
                target.latitude = None
                target.longitude = None
                target.address_provider = None
                target.provider_place_id = None
                target.address_verification_status = (
                    "pending"
                )
            target.row_version += 1
            return target

    class FakeClientRepository:
        async def get_active_client(
            self,
            **kwargs,
        ):
            calls.append(("client", kwargs))
            return SimpleNamespace(
                id=new_client_id,
                tenant_id=tenant_id,
                deleted_at=None,
            )

    class FakeAccessRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            calls.append(("membership", kwargs))
            return True

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeProjectRepository(),
        client_repository=FakeClientRepository(),
        access_repository=FakeAccessRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.update_project(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        expected_row_version=3,
        name="  Updated project  ",
        client_id=new_client_id,
        responsible_user_id=(
            new_responsible_id
        ),
        comment="Updated comment",
        address_raw="  New address  ",
        trace_id="trace-con-004-update",
    )

    assert result is project
    assert result.name == "Updated project"
    assert result.client_id == new_client_id
    assert (
        result.responsible_user_id
        == new_responsible_id
    )
    assert result.comment == "Updated comment"
    assert result.address_raw == "New address"
    assert result.address_formatted is None
    assert result.country_code is None
    assert result.region is None
    assert result.city is None
    assert result.postal_code is None
    assert result.latitude is None
    assert result.longitude is None
    assert result.address_provider is None
    assert result.provider_place_id is None
    assert (
        result.address_verification_status
        == "pending"
    )
    assert result.row_version == 4

    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "client",
        "membership",
        "update",
        "event",
        "commit",
    ]

    event_kwargs = next(
        kwargs
        for name, kwargs in calls
        if name == "event"
    )
    assert event_kwargs["event_type"] == (
        "construction_project_updated"
    )
    assert event_kwargs["tenant_id"] == tenant_id
    assert event_kwargs["user_id"] == user_id
    assert event_kwargs["entity_type"] == (
        "construction_project"
    )
    assert event_kwargs["entity_id"] == project_id
    assert event_kwargs["trace_id"] == (
        "trace-con-004-update"
    )

    changes = {
        change["field"]: change
        for change in (
            event_kwargs["payload"]["changes"]
        )
    }
    assert changes["name"] == {
        "field": "name",
        "old_value": "Old project",
        "new_value": "Updated project",
    }
    assert changes["client_id"] == {
        "field": "client_id",
        "old_value": str(old_client_id),
        "new_value": str(new_client_id),
    }
    assert changes["responsible_user_id"] == {
        "field": "responsible_user_id",
        "old_value": str(
            old_responsible_id
        ),
        "new_value": str(
            new_responsible_id
        ),
    }
    assert changes["address_raw"] == {
        "field": "address_raw",
        "old_value": "Old address",
        "new_value": "New address",
    }

    reset_fields = {
        "address_formatted",
        "country_code",
        "region",
        "city",
        "postal_code",
        "latitude",
        "longitude",
        "address_provider",
        "provider_place_id",
        "address_verification_status",
    }
    assert reset_fields <= changes.keys()
    assert (
        changes[
            "address_verification_status"
        ]["new_value"]
        == "pending"
    )


@pytest.mark.asyncio
async def test_con_004_project_update_noop_preserves_verified_state_and_row_version():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    client_id = uuid4()
    responsible_user_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        name="Current project",
        client_id=client_id,
        responsible_user_id=(
            responsible_user_id
        ),
        comment="Current comment",
        address_raw="Current address",
        address_formatted=(
            "Verified current address"
        ),
        country_code="PT",
        region="Lisbon",
        city="Lisbon",
        postal_code="1000-001",
        latitude="38.7223",
        longitude="-9.1393",
        address_provider="provider",
        provider_place_id="place-001",
        address_verification_status=(
            "verified"
        ),
        status="planning",
        deleted_at=None,
        row_version=7,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return project

        async def update_project(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "NO-OP PROJECT MUST NOT BE UPDATED"
            )

    class ForbiddenClientRepository:
        async def get_active_client(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "UNCHANGED CLIENT MUST NOT "
                "BE REVALIDATED"
            )

    class ForbiddenAccessRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "UNCHANGED RESPONSIBLE USER MUST "
                "NOT BE REVALIDATED"
            )

    class ForbiddenEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "NO-OP PROJECT MUST NOT BE AUDITED"
            )

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeProjectRepository(),
        client_repository=(
            ForbiddenClientRepository()
        ),
        access_repository=(
            ForbiddenAccessRepository()
        ),
        event_repository=(
            ForbiddenEventRepository()
        ),
    )

    result = await service.update_project(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        expected_row_version=7,
        name="  Current project  ",
        client_id=client_id,
        responsible_user_id=(
            responsible_user_id
        ),
        comment="Current comment",
        address_raw="  Current address  ",
        trace_id="trace-con-004-noop",
    )

    assert result is project
    assert result.row_version == 7
    assert result.address_formatted == (
        "Verified current address"
    )
    assert (
        result.address_verification_status
        == "verified"
    )
    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "commit",
    ]


@pytest.mark.asyncio
async def test_con_004_project_status_rejects_stale_row_version_before_update():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_foundation import (
        ConstructionRowVersionConflictError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="draft",
        deleted_at=None,
        row_version=3,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return project

        async def set_project_status(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE PROJECT MUST NOT BE UPDATED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE PROJECT MUST NOT BE AUDITED"
            )

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionRowVersionConflictError,
    ) as exc_info:
        await service.transition_project_status(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            next_status="planning",
            expected_row_version=2,
            trace_id=(
                "trace-con-004-stale-status"
            ),
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.code
        == "ROW_VERSION_CONFLICT"
    )
    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "rollback",
    ]


@pytest.mark.asyncio
async def test_con_004_project_status_noop_preserves_row_version_without_audit():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="planning",
        deleted_at=None,
        row_version=4,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return project

        async def set_project_status(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "NO-OP STATUS MUST NOT BE UPDATED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "NO-OP STATUS MUST NOT BE AUDITED"
            )

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.transition_project_status(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        next_status="planning",
        expected_row_version=4,
        trace_id=(
            "trace-con-004-status-noop"
        ),
    )

    assert result is project
    assert result.status == "planning"
    assert result.row_version == 4
    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "commit",
    ]


def test_con_004_project_status_transitions_match_confirmed_workflow():
    import pytest

    from services.construction_projects import (
        CONSTRUCTION_PROJECT_STATUS_TRANSITIONS,
        ConstructionProjectTransitionError,
        require_project_status_transition,
    )

    assert (
        CONSTRUCTION_PROJECT_STATUS_TRANSITIONS
        == {
            "draft": frozenset(
                {
                    "planning",
                    "cancelled",
                }
            ),
            "planning": frozenset(
                {
                    "active",
                    "cancelled",
                }
            ),
            "active": frozenset(
                {
                    "completed",
                    "cancelled",
                }
            ),
            "completed": frozenset(),
            "cancelled": frozenset(),
        }
    )

    for current_status, next_statuses in (
        CONSTRUCTION_PROJECT_STATUS_TRANSITIONS
        .items()
    ):
        for next_status in next_statuses:
            require_project_status_transition(
                current_status=current_status,
                next_status=next_status,
            )

    forbidden = (
        ("draft", "active"),
        ("draft", "completed"),
        ("planning", "completed"),
        ("active", "planning"),
        ("completed", "active"),
        ("completed", "cancelled"),
        ("cancelled", "draft"),
        ("draft", "draft"),
    )

    for current_status, next_status in forbidden:
        with pytest.raises(
            ConstructionProjectTransitionError
        ):
            require_project_status_transition(
                current_status=current_status,
                next_status=next_status,
            )

@pytest.mark.asyncio
async def test_con_004_repository_locks_active_tenant_project_before_status_change():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionProjectRepository,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(
            self,
            statement,
        ):
            statements.append(statement)
            return FakeResult()

    repository = ConstructionProjectRepository(
        FakeSession()
    )

    result = (
        await repository
        .get_active_project_for_update(
            tenant_id=tenant_id,
            project_id=project_id,
        )
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(project_id).upper() in sql
    assert "CONSTRUCTION_PROJECTS.TENANT_ID" in sql
    assert "CONSTRUCTION_PROJECTS.ID" in sql
    assert "CONSTRUCTION_PROJECTS.DELETED_AT IS NULL" in sql
    assert "FOR UPDATE" in sql

@pytest.mark.asyncio
async def test_con_004_repository_updates_locked_project_status_without_internal_commit():
    from types import SimpleNamespace

    from database.repositories.construction import (
        ConstructionProjectRepository,
    )

    project = SimpleNamespace(
        status="draft",
    )
    calls = []

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionProjectRepository(
        FakeSession()
    )

    result = await repository.set_project_status(
        project=project,
        status="planning",
    )

    assert result is project
    assert project.status == "planning"
    assert calls == [
        (
            "flush",
            {},
        ),
    ]

@pytest.mark.asyncio
async def test_con_004_project_service_transitions_locked_project_in_verified_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="draft",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return project

        async def set_project_status(
            self,
            **kwargs,
        ):
            calls.append(("set", kwargs))
            kwargs["project"].status = (
                kwargs["status"]
            )
            kwargs["project"].row_version += 1
            return kwargs["project"]

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEventRepository(),
    )

    result = (
        await service
        .transition_project_status(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            next_status="planning",
            expected_row_version=1,
            trace_id=(
                "trace-con-004-status"
            ),
        )
    )

    assert result is project
    assert result.status == "planning"
    assert result.row_version == 2
    assert calls == [
        (
            "get",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "set",
            {
                "project": project,
                "status": "planning",
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_project_"
                    "status_changed"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project"
                ),
                "entity_id": project_id,
                "payload": {
                    "operation": (
                        "status_change"
                    ),
                    "changes": [
                        {
                            "field": "status",
                            "old_value": "draft",
                            "new_value": (
                                "planning"
                            ),
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-004-status"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]

def test_con_005_project_area_model_matches_release_contract():
    from sqlalchemy import CheckConstraint

    from database.models import (
        ConstructionProjectArea,
    )

    table = ConstructionProjectArea.__table__

    assert table.name == (
        "construction_project_areas"
    )
    assert {
        column.name
        for column in table.columns
    } == {
        "id",
        "tenant_id",
        "project_id",
        "area_type",
        "name",
        "sort_order",
        "status",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
        "row_version",
    }

    assert not table.c.tenant_id.nullable
    assert not table.c.project_id.nullable
    assert not table.c.area_type.nullable
    assert not table.c.name.nullable
    assert table.c.sort_order.nullable
    assert table.c.sort_order.default is None
    assert not table.c.status.nullable
    assert not table.c.created_by.nullable
    assert not table.c.created_at.nullable
    assert not table.c.updated_at.nullable
    assert table.c.deleted_at.nullable
    assert not table.c.row_version.nullable

    checks = {
        constraint.name: " ".join(
            str(constraint.sqltext).split()
        ).upper()
        for constraint in table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    area_type_check = checks[
        "ck_construction_project_areas_type"
    ]
    for area_type in (
        "ROOM",
        "PROJECT_GENERAL",
        "EXTERIOR",
        "OTHER",
    ):
        assert f"'{area_type}'" in area_type_check

    status_check = checks[
        "ck_construction_project_areas_status"
    ]
    for status in (
        "DRAFT",
        "MEASURED",
        "CONFIRMED",
    ):
        assert f"'{status}'" in status_check

    foreign_key_targets = {
        tuple(
            foreign_key.target_fullname
            for foreign_key
            in constraint.elements
        )
        for constraint
        in table.foreign_key_constraints
    }

    assert (
        "construction_projects.tenant_id",
        "construction_projects.id",
    ) in foreign_key_targets
    assert (
        "users.id",
    ) in foreign_key_targets

    assert "measurement_id" not in table.columns


@pytest.mark.asyncio
async def test_con_005_repository_creates_tenant_project_area_without_internal_commit():
    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    actor_user_id = uuid4()
    calls = []

    class FakeSession:
        def add(self, value):
            calls.append(("add", value))

        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            calls.append(("commit", {}))

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = await repository.create_area(
        tenant_id=tenant_id,
        project_id=project_id,
        area_type="ROOM",
        name="Спальня 2",
        sort_order=None,
        created_by=actor_user_id,
    )

    assert result.tenant_id == tenant_id
    assert result.project_id == project_id
    assert result.area_type == "ROOM"
    assert result.name == "Спальня 2"
    assert result.sort_order is None
    assert result.status == "draft"
    assert result.created_by == actor_user_id
    assert result.deleted_at is None
    assert result.row_version == 1

    assert calls == [
        ("add", result),
        ("flush", {}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_005_area_creation_rejects_terminal_project(
    project_status,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status=project_status,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def create_area(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE CREATED "
                "IN TERMINAL PROJECT"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED AREA CREATION "
                "MUST NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError,
    ):
        await service.create_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_type="ROOM",
            name="Bedroom",
            sort_order=None,
            trace_id=(
                "trace-con-005-terminal-project"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_area_service_creates_draft_in_verified_project_scope():
    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    expected = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project(
            self,
            **kwargs,
        ):
            calls.append(("get_project", kwargs))
            return project

    class FakeAreaRepository:
        async def create_area(
            self,
            **kwargs,
        ):
            calls.append(("create_area", kwargs))
            return expected

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    result = await service.create_area(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        area_type="ROOM",
        name="  Спальня 2  ",
        sort_order=None,
        trace_id="trace-con-005-create",
    )

    assert result is expected
    assert calls == [
        (
            "get_project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "create_area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_type": "ROOM",
                "name": "Спальня 2",
                "sort_order": None,
                "created_by": user_id,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_project_area_created"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project_area"
                ),
                "entity_id": area_id,
                "payload": {
                    "operation": "create",
                    "changes": [],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-005-create"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_repository_gets_active_project_for_area_in_tenant_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionProjectRepository,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(
            self,
            statement,
        ):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionProjectRepository(
        FakeSession()
    )

    result = await repository.get_active_project(
        tenant_id=tenant_id,
        project_id=project_id,
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(project_id).upper() in sql
    assert "CONSTRUCTION_PROJECTS.TENANT_ID" in sql
    assert "CONSTRUCTION_PROJECTS.ID" in sql
    assert (
        "CONSTRUCTION_PROJECTS.DELETED_AT IS NULL"
        in sql
    )
    assert "FOR UPDATE" not in sql


def test_con_005_project_area_status_transitions_require_explicit_reopening():
    from services.construction_areas import (
        ConstructionProjectAreaTransitionError,
        require_project_area_status_transition,
    )

    allowed = (
        ("draft", "measured", False),
        ("measured", "confirmed", False),
        ("measured", "draft", False),
        ("confirmed", "draft", True),
    )

    for (
        current_status,
        next_status,
        reopening,
    ) in allowed:
        require_project_area_status_transition(
            current_status=current_status,
            next_status=next_status,
            reopening=reopening,
        )

    with pytest.raises(
        ConstructionProjectAreaTransitionError
    ):
        require_project_area_status_transition(
            current_status="confirmed",
            next_status="draft",
            reopening=False,
        )

    with pytest.raises(
        ConstructionProjectAreaTransitionError
    ):
        require_project_area_status_transition(
            current_status="draft",
            next_status="confirmed",
            reopening=False,
        )

    with pytest.raises(
        ConstructionProjectAreaTransitionError
    ):
        require_project_area_status_transition(
            current_status="unknown",
            next_status="draft",
            reopening=True,
        )


@pytest.mark.asyncio
async def test_con_005_repository_locks_active_area_in_full_project_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(
            self,
            statement,
        ):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = (
        await repository
        .get_active_area_for_update(
            tenant_id=tenant_id,
            project_id=project_id,
            area_id=area_id,
        )
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(project_id).upper() in sql
    assert str(area_id).upper() in sql
    assert (
        "CONSTRUCTION_PROJECT_AREAS.TENANT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_PROJECT_AREAS.PROJECT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_PROJECT_AREAS.ID"
        in sql
    )
    assert (
        "CONSTRUCTION_PROJECT_AREAS."
        "DELETED_AT IS NULL"
        in sql
    )
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_con_005_repository_updates_locked_area_status_without_internal_commit():
    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    calls = []
    area = SimpleNamespace(
        status="draft",
    )

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            calls.append(("commit", {}))

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = await repository.set_area_status(
        area=area,
        status="measured",
    )

    assert result is area
    assert area.status == "measured"
    assert calls == [
        ("flush", {}),
    ]


@pytest.mark.asyncio
async def test_con_005_area_reopen_rejects_stale_row_version_before_transition():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionRowVersionConflictError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="confirmed",
        deleted_at=None,
        row_version=9,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def set_area_status(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA MUST NOT BE REOPENED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE REOPEN MUST NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionRowVersionConflictError,
    ):
        await service.reopen_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=8,
            trace_id=(
                "trace-con-005-stale-reopen"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "rollback",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_005_area_reopen_rejects_terminal_project(
    project_status,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status=project_status,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE LOCKED "
                "AFTER TERMINAL PROJECT CHECK"
            )

        async def set_area_status(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE REOPENED "
                "IN TERMINAL PROJECT"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED REOPEN MUST "
                "NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError,
    ):
        await service.reopen_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            trace_id=(
                "trace-con-005-terminal-reopen"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_area_service_reopens_confirmed_area_through_explicit_operation():
    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="confirmed",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return area

        async def set_area_status(
            self,
            **kwargs,
        ):
            calls.append(("set", kwargs))
            kwargs["area"].status = (
                kwargs["status"]
            )
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.reopen_area(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        area_id=area_id,
        expected_row_version=1,
        trace_id="trace-con-005-reopen",
    )

    assert result is area
    assert result.status == "draft"
    assert result.row_version == 2
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "get",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": area_id,
            },
        ),
        (
            "set",
            {
                "area": area,
                "status": "draft",
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_project_area_reopened"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project_area"
                ),
                "entity_id": area_id,
                "payload": {
                    "operation": "reopen",
                    "changes": [
                        {
                            "field": "status",
                            "old_value": (
                                "confirmed"
                            ),
                            "new_value": "draft",
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-005-reopen"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_area_status_rejects_stale_row_version_before_transition():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionRowVersionConflictError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
        row_version=4,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def set_area_status(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA STATUS MUST "
                "NOT BE UPDATED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA STATUS MUST "
                "NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionRowVersionConflictError,
    ):
        await service.transition_area_status(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=3,
            next_status="measured",
            trace_id=(
                "trace-con-005-stale-status"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "rollback",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_005_area_status_rejects_terminal_project(
    project_status,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status=project_status,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE LOCKED "
                "AFTER TERMINAL PROJECT CHECK"
            )

        async def set_area_status(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA STATUS MUST NOT CHANGE "
                "IN TERMINAL PROJECT"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED STATUS CHANGE "
                "MUST NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError,
    ):
        await service.transition_area_status(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            next_status="measured",
            expected_row_version=1,
            trace_id=(
                "trace-con-005-terminal-status"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_area_service_transitions_locked_area_in_verified_project_scope():
    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return area

        async def set_area_status(
            self,
            **kwargs,
        ):
            calls.append(("set", kwargs))
            kwargs["area"].status = (
                kwargs["status"]
            )
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.transition_area_status(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        area_id=area_id,
        expected_row_version=1,
        next_status="measured",
        trace_id="trace-con-005-status",
    )

    assert result is area
    assert result.status == "measured"
    assert result.row_version == 2
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "get",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": area_id,
            },
        ),
        (
            "set",
            {
                "area": area,
                "status": "measured",
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_project_area_"
                    "status_changed"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project_area"
                ),
                "entity_id": area_id,
                "payload": {
                    "operation": (
                        "status_change"
                    ),
                    "changes": [
                        {
                            "field": "status",
                            "old_value": "draft",
                            "new_value": (
                                "measured"
                            ),
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-005-status"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_repository_renames_locked_area_without_changing_identity():
    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        name="Спальня",
        status="draft",
    )

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            calls.append(("commit", {}))

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = await repository.rename_area(
        area=area,
        name="Спальня 2",
    )

    assert result is area
    assert result.id == area_id
    assert result.tenant_id == tenant_id
    assert result.project_id == project_id
    assert result.name == "Спальня 2"
    assert result.status == "draft"
    assert calls == [
        ("flush", {}),
    ]


@pytest.mark.asyncio
async def test_con_005_area_rename_rejects_stale_row_version_before_update():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionRowVersionConflictError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        name="Current area",
        status="draft",
        deleted_at=None,
        row_version=5,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def rename_area(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA MUST NOT BE RENAMED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA MUST NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionRowVersionConflictError,
    ) as exc_info:
        await service.rename_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=4,
            name="Updated area",
            trace_id=(
                "trace-con-005-stale-rename"
            ),
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.code
        == "ROW_VERSION_CONFLICT"
    )
    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "rollback",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_005_area_rename_rejects_terminal_project(
    project_status,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status=project_status,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE LOCKED "
                "AFTER TERMINAL PROJECT CHECK"
            )

        async def rename_area(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE RENAMED "
                "IN TERMINAL PROJECT"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED AREA RENAME "
                "MUST NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError,
    ):
        await service.rename_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            name="Updated room",
            expected_row_version=1,
            trace_id=(
                "trace-con-005-terminal-rename"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_area_service_renames_locked_area_in_verified_project_scope():
    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        name="Спальня",
        status="draft",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return area

        async def rename_area(
            self,
            **kwargs,
        ):
            calls.append(("rename", kwargs))
            kwargs["area"].name = kwargs["name"]
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.rename_area(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        area_id=area_id,
        expected_row_version=1,
        name="  Спальня 2  ",
        trace_id="trace-con-005-rename",
    )

    assert result is area
    assert result.name == "Спальня 2"
    assert result.row_version == 2
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "get",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": area_id,
            },
        ),
        (
            "rename",
            {
                "area": area,
                "name": "Спальня 2",
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_project_area_updated"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project_area"
                ),
                "entity_id": area_id,
                "payload": {
                    "operation": "update",
                    "changes": [
                        {
                            "field": "name",
                            "old_value": "Спальня",
                            "new_value": (
                                "Спальня 2"
                            ),
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-005-rename"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_repository_archives_locked_area_without_physical_delete():
    from datetime import UTC, datetime

    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    deleted_at = datetime(
        2026,
        9,
        17,
        15,
        0,
        tzinfo=UTC,
    )
    calls = []
    area = SimpleNamespace(
        deleted_at=None,
    )

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def delete(self, value):
            calls.append(("delete", value))

        async def commit(self):
            calls.append(("commit", {}))

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = await repository.soft_delete_area(
        area=area,
        deleted_at=deleted_at,
    )

    assert result is area
    assert result.deleted_at == deleted_at
    assert calls == [
        ("flush", {}),
    ]


@pytest.mark.asyncio
async def test_con_005_repository_detects_active_elements_before_area_archive():
    from uuid import uuid4

    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    tenant_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return element_id

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult()

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = await repository.has_active_elements(
        tenant_id=tenant_id,
        project_area_id=project_area_id,
    )

    assert result is True
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            compile_kwargs={
                "literal_binds": True,
            }
        )
    ).upper()

    assert "CONSTRUCTION_AREA_ELEMENTS" in sql
    assert "TENANT_ID" in sql
    assert "PROJECT_AREA_ID" in sql
    assert "DELETED_AT IS NULL" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_005_area_archive_rejects_terminal_project(
    project_status,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status=project_status,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE LOCKED "
                "AFTER TERMINAL PROJECT CHECK"
            )

        async def has_active_elements(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "ELEMENTS MUST NOT BE CHECKED "
                "AFTER TERMINAL PROJECT CHECK"
            )

        async def soft_delete_area(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE ARCHIVED "
                "IN TERMINAL PROJECT"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED ARCHIVE MUST "
                "NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError,
    ):
        await service.archive_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            trace_id=(
                "trace-con-005-terminal-archive"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_area_archive_rejects_active_elements_without_cascade():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaHasActiveElementsError,
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
        row_version=3,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def has_active_elements(
            self,
            **kwargs,
        ):
            calls.append(
                ("active_elements", kwargs)
            )
            return True

        async def soft_delete_area(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA WITH ACTIVE ELEMENTS "
                "MUST NOT BE ARCHIVED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED AREA ARCHIVE MUST "
                "NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectAreaHasActiveElementsError,
    ):
        await service.archive_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=3,
            trace_id=(
                "trace-con-005-active-elements"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "lock",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": area_id,
            },
        ),
        (
            "active_elements",
            {
                "tenant_id": tenant_id,
                "project_area_id": area_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_confirmed_area_requires_reopening_before_archive():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
        ConstructionProjectAreaTransitionError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="confirmed",
        deleted_at=None,
        row_version=3,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def soft_delete_area(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "CONFIRMED AREA MUST NOT "
                "BE ARCHIVED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED ARCHIVE MUST "
                "NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectAreaTransitionError,
    ):
        await service.archive_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=3,
            trace_id=(
                "trace-con-005-confirmed-archive"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "rollback",
    ]


@pytest.mark.asyncio
async def test_con_005_area_archive_rejects_stale_row_version_before_update():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionRowVersionConflictError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
        row_version=5,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def soft_delete_area(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA MUST NOT BE ARCHIVED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA ARCHIVE MUST "
                "NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionRowVersionConflictError,
    ):
        await service.archive_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=4,
            trace_id=(
                "trace-con-005-stale-archive"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "rollback",
    ]


@pytest.mark.asyncio
async def test_con_005_area_service_archives_locked_area_in_verified_project_scope():
    from datetime import UTC, datetime

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    deleted_at = datetime(
        2026,
        9,
        17,
        16,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return area

        async def has_active_elements(
            self,
            **kwargs,
        ):
            calls.append(
                ("active_elements", kwargs)
            )
            return False

        async def soft_delete_area(
            self,
            **kwargs,
        ):
            calls.append(("archive", kwargs))
            kwargs["area"].deleted_at = (
                kwargs["deleted_at"]
            )
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
        now_provider=lambda: deleted_at,
    )

    result = await service.archive_area(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        area_id=area_id,
        expected_row_version=1,
        trace_id="trace-con-005-archive",
    )

    assert result is area
    assert result.deleted_at == deleted_at
    assert result.row_version == 2
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "get",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": area_id,
            },
        ),
        (
            "active_elements",
            {
                "tenant_id": tenant_id,
                "project_area_id": area_id,
            },
        ),
        (
            "archive",
            {
                "area": area,
                "deleted_at": deleted_at,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_project_area_archived"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project_area"
                ),
                "entity_id": area_id,
                "payload": {
                    "operation": "archive",
                    "changes": [
                        {
                            "field": "deleted_at",
                            "old_value": None,
                            "new_value": (
                                deleted_at.isoformat()
                            ),
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-005-archive"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_repository_gets_only_active_area_in_full_project_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(
            self,
            statement,
        ):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = await repository.get_active_area(
        tenant_id=tenant_id,
        project_id=project_id,
        area_id=area_id,
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(project_id).upper() in sql
    assert str(area_id).upper() in sql
    assert (
        "CONSTRUCTION_PROJECT_AREAS.TENANT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_PROJECT_AREAS.PROJECT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_PROJECT_AREAS.ID"
        in sql
    )
    assert (
        "CONSTRUCTION_PROJECT_AREAS."
        "DELETED_AT IS NULL"
        in sql
    )
    assert "FOR UPDATE" not in sql


@pytest.mark.asyncio
async def test_con_005_area_service_reads_active_area_in_verified_project_scope():
    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OBSERVER",),
        permissions=(
            "construction.projects.read",
        ),
        grants=(
            ConstructionGrantContext(
                role="OBSERVER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    expected = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            raise AssertionError(
                "Read operation must not commit."
            )

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_area(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return expected

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=object(),
    )

    result = await service.get_area(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        area_id=area_id,
    )

    assert result is expected
    assert calls == [
        (
            "get",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": area_id,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_generic_transition_cannot_reopen_confirmed_area():
    from services.construction_areas import (
        ConstructionProjectAreaService,
        ConstructionProjectAreaTransitionError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="confirmed",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return area

        async def set_area_status(
            self,
            **kwargs,
        ):
            calls.append(("set", kwargs))
            return kwargs["area"]

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
    )

    with pytest.raises(
        ConstructionProjectAreaTransitionError
    ):
        await service.transition_area_status(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            next_status="draft",
        )

    assert area.status == "confirmed"
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "get",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": area_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_repository_lists_active_areas_in_confirmed_deterministic_order():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    tenant_id = uuid4()
    project_id = uuid4()
    expected = [
        object(),
        object(),
    ]
    statements = []

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        async def execute(
            self,
            statement,
        ):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = await repository.list_active_areas(
        tenant_id=tenant_id,
        project_id=project_id,
    )

    assert result == expected
    assert len(statements) == 1

    sql = " ".join(
        str(
            statements[0].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={
                    "literal_binds": True,
                },
            )
        )
        .upper()
        .split()
    )

    assert str(tenant_id).upper() in sql
    assert str(project_id).upper() in sql
    assert (
        "CONSTRUCTION_PROJECT_AREAS."
        "DELETED_AT IS NULL"
        in sql
    )
    assert (
        "ORDER BY "
        "CONSTRUCTION_PROJECT_AREAS.SORT_ORDER "
        "ASC NULLS LAST, "
        "CONSTRUCTION_PROJECT_AREAS.CREATED_AT ASC, "
        "CONSTRUCTION_PROJECT_AREAS.ID ASC"
        in sql
    )


@pytest.mark.asyncio
async def test_con_005_area_service_lists_active_areas_in_verified_project_scope():
    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OBSERVER",),
        permissions=(
            "construction.projects.read",
        ),
        grants=(
            ConstructionGrantContext(
                role="OBSERVER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )

    class FakeSession:
        async def commit(self):
            raise AssertionError(
                "Read operation must not commit."
            )

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def list_active_areas(
            self,
            **kwargs,
        ):
            calls.append(("list", kwargs))
            return expected

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=object(),
    )

    result = await service.list_areas(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
    )

    assert result is expected
    assert calls == [
        (
            "list",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_repository_updates_locked_area_sort_order_without_reordering():
    from database.repositories.construction import (
        ConstructionProjectAreaRepository,
    )

    calls = []
    area = SimpleNamespace(
        sort_order=None,
    )

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            calls.append(("commit", {}))

    repository = ConstructionProjectAreaRepository(
        FakeSession()
    )

    result = await repository.set_area_sort_order(
        area=area,
        sort_order=10,
    )

    assert result is area
    assert result.sort_order == 10
    assert calls == [
        ("flush", {}),
    ]

    calls.clear()

    result = await repository.set_area_sort_order(
        area=area,
        sort_order=None,
    )

    assert result is area
    assert result.sort_order is None
    assert calls == [
        ("flush", {}),
    ]


@pytest.mark.asyncio
async def test_con_005_area_sort_order_rejects_stale_row_version_before_update():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionRowVersionConflictError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        sort_order=4,
        status="confirmed",
        deleted_at=None,
        row_version=6,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def set_area_sort_order(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA SORT ORDER MUST "
                "NOT BE UPDATED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE AREA SORT ORDER MUST "
                "NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionRowVersionConflictError,
    ):
        await service.update_area_sort_order(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=5,
            sort_order=None,
            trace_id=(
                "trace-con-005-stale-sort"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "rollback",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_005_area_sort_order_rejects_terminal_project(
    project_status,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status=project_status,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE LOCKED "
                "AFTER TERMINAL PROJECT CHECK"
            )

        async def set_area_sort_order(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "SORT ORDER MUST NOT CHANGE "
                "IN TERMINAL PROJECT"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED SORT ORDER CHANGE "
                "MUST NOT BE AUDITED"
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError,
    ):
        await service.update_area_sort_order(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            sort_order=7,
            expected_row_version=1,
            trace_id=(
                "trace-con-005-terminal-sort"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_005_area_service_updates_sort_order_without_reordering_other_areas():
    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        sort_order=5,
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get", kwargs))
            return area

        async def set_area_sort_order(
            self,
            **kwargs,
        ):
            calls.append(("set", kwargs))
            kwargs["area"].sort_order = (
                kwargs["sort_order"]
            )
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.update_area_sort_order(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        area_id=area_id,
        expected_row_version=1,
        sort_order=None,
        trace_id="trace-con-005-sort",
    )

    assert result is area
    assert result.sort_order is None
    assert result.row_version == 2
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "get",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": area_id,
            },
        ),
        (
            "set",
            {
                "area": area,
                "sort_order": None,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_project_area_updated"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project_area"
                ),
                "entity_id": area_id,
                "payload": {
                    "operation": "update",
                    "changes": [
                        {
                            "field": "sort_order",
                            "old_value": 5,
                            "new_value": None,
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-005-sort"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]


def test_con_006_element_types_and_geometry_match_confirmed_contract():
    from services.construction_elements import (
        CONSTRUCTION_AREA_ELEMENT_TYPES,
        ConstructionElementGeometryError,
        validate_construction_geometry,
    )

    assert CONSTRUCTION_AREA_ELEMENT_TYPES == frozenset(
        {
            "WALL",
            "SLAB",
            "CEILING",
            "FLOOR",
            "WINDOW",
            "DOOR",
            "NICHE",
            "OPENING",
            "ENGINEERING_POINT",
            "LINEAR_ROUTE",
            "OTHER",
        }
    )

    geometry = {
        "schema_version": 1,
        "future_element_specific_data": {
            "value": "preserved",
        },
    }

    assert (
        validate_construction_geometry(
            geometry
        )
        is geometry
    )

    invalid_geometries = (
        None,
        [],
        "geometry",
        {},
        {
            "schema_version": True,
        },
        {
            "schema_version": "1",
        },
        {
            "schema_version": 2,
        },
    )

    for invalid_geometry in invalid_geometries:
        with pytest.raises(
            ConstructionElementGeometryError
        ):
            validate_construction_geometry(
                invalid_geometry
            )


def test_con_006_parent_element_contract_requires_same_area_wall():
    from services.construction_elements import (
        CONSTRUCTION_PARENT_CAPABLE_ELEMENT_TYPES,
        ConstructionElementParentError,
        validate_construction_parent_element,
    )

    tenant_id = uuid4()
    project_area_id = uuid4()

    wall = SimpleNamespace(
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
    )

    assert (
        CONSTRUCTION_PARENT_CAPABLE_ELEMENT_TYPES
        == frozenset(
            {
                "WINDOW",
                "DOOR",
                "OPENING",
            }
        )
    )

    for element_type in (
        "WINDOW",
        "DOOR",
        "OPENING",
    ):
        assert (
            validate_construction_parent_element(
                element_type=element_type,
                tenant_id=tenant_id,
                project_area_id=project_area_id,
                parent_element=None,
            )
            is None
        )
        assert (
            validate_construction_parent_element(
                element_type=element_type,
                tenant_id=tenant_id,
                project_area_id=project_area_id,
                parent_element=wall,
            )
            is wall
        )

    invalid_cases = (
        (
            "FLOOR",
            wall,
        ),
        (
            "WINDOW",
            SimpleNamespace(
                tenant_id=uuid4(),
                project_area_id=project_area_id,
                element_type="WALL",
            ),
        ),
        (
            "DOOR",
            SimpleNamespace(
                tenant_id=tenant_id,
                project_area_id=uuid4(),
                element_type="WALL",
            ),
        ),
        (
            "OPENING",
            SimpleNamespace(
                tenant_id=tenant_id,
                project_area_id=project_area_id,
                element_type="CEILING",
            ),
        ),
    )

    for element_type, parent_element in invalid_cases:
        with pytest.raises(
            ConstructionElementParentError
        ):
            validate_construction_parent_element(
                element_type=element_type,
                tenant_id=tenant_id,
                project_area_id=project_area_id,
                parent_element=parent_element,
            )


def test_con_006_area_element_model_matches_confirmed_physical_contract():
    from sqlalchemy import (
        CheckConstraint,
        UniqueConstraint,
    )
    from sqlalchemy.dialects.postgresql import JSONB

    from database.models import (
        ConstructionAreaElement,
    )

    table = ConstructionAreaElement.__table__

    assert table.name == (
        "construction_area_elements"
    )
    assert {
        column.name
        for column in table.columns
    } == {
        "id",
        "tenant_id",
        "project_area_id",
        "parent_element_id",
        "element_type",
        "name",
        "sort_order",
        "geometry_json",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
        "row_version",
    }

    for column_name in (
        "id",
        "tenant_id",
        "project_area_id",
        "element_type",
        "name",
        "geometry_json",
        "created_by",
        "created_at",
        "updated_at",
        "row_version",
    ):
        assert not table.c[column_name].nullable

    for column_name in (
        "parent_element_id",
        "sort_order",
        "deleted_at",
    ):
        assert table.c[column_name].nullable

    assert table.c.sort_order.default is None
    assert table.c.geometry_json.default is None
    assert isinstance(
        table.c.geometry_json.type,
        JSONB,
    )

    checks = {
        constraint.name: " ".join(
            str(constraint.sqltext).split()
        ).upper()
        for constraint in table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    type_check = checks[
        "ck_construction_area_elements_type"
    ]
    for element_type in (
        "WALL",
        "SLAB",
        "CEILING",
        "FLOOR",
        "WINDOW",
        "DOOR",
        "NICHE",
        "OPENING",
        "ENGINEERING_POINT",
        "LINEAR_ROUTE",
        "OTHER",
    ):
        assert f"'{element_type}'" in type_check

    assert "BTRIM(NAME)" in checks[
        "ck_construction_area_elements_name"
    ]

    geometry_check = checks[
        "ck_construction_area_elements_geometry"
    ]
    assert "JSONB_TYPEOF(GEOMETRY_JSON)" in (
        geometry_check
    )
    assert "SCHEMA_VERSION" in geometry_check
    assert "'1'" in geometry_check

    parent_check = checks[
        "ck_construction_area_elements_parent_type"
    ]
    assert "PARENT_ELEMENT_ID IS NULL" in (
        parent_check
    )
    for element_type in (
        "WINDOW",
        "DOOR",
        "OPENING",
    ):
        assert f"'{element_type}'" in parent_check

    foreign_key_targets = {
        tuple(
            foreign_key.target_fullname
            for foreign_key
            in constraint.elements
        )
        for constraint
        in table.foreign_key_constraints
    }

    assert (
        "construction_project_areas.tenant_id",
        "construction_project_areas.id",
    ) in foreign_key_targets

    assert (
        "construction_area_elements.tenant_id",
        "construction_area_elements.project_area_id",
        "construction_area_elements.id",
    ) in foreign_key_targets

    assert (
        "users.id",
    ) in foreign_key_targets

    unique_column_sets = {
        tuple(
            column.name
            for column in constraint.columns
        )
        for constraint in table.constraints
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "tenant_id",
        "project_area_id",
        "id",
    ) in unique_column_sets

    assert not any(
        "name" in column_names
        or "sort_order" in column_names
        for column_names in unique_column_sets
    )




@pytest.mark.asyncio
async def test_con_006_repository_creates_scoped_area_element_without_internal_commit():
    from database.repositories.construction import (
        ConstructionAreaElementRepository,
    )

    tenant_id = uuid4()
    project_area_id = uuid4()
    actor_user_id = uuid4()
    geometry_json = {
        "schema_version": 1,
        "width": "1.25",
        "future_data": {
            "preserved": True,
        },
    }
    calls = []

    class FakeSession:
        def add(self, value):
            calls.append(("add", value))

        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            calls.append(("commit", {}))

    repository = ConstructionAreaElementRepository(
        FakeSession()
    )

    result = await repository.create_element(
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        parent_element_id=None,
        element_type="WALL",
        name="External wall",
        sort_order=None,
        geometry_json=geometry_json,
        created_by=actor_user_id,
    )

    assert result.tenant_id == tenant_id
    assert result.project_area_id == project_area_id
    assert result.parent_element_id is None
    assert result.element_type == "WALL"
    assert result.name == "External wall"
    assert result.sort_order is None
    assert result.geometry_json is geometry_json
    assert result.created_by == actor_user_id
    assert result.deleted_at is None
    assert result.row_version == 1

    assert calls == [
        ("add", result),
        ("flush", {}),
    ]



@pytest.mark.asyncio
async def test_con_006_repository_gets_active_parent_wall_in_same_area_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionAreaElementRepository,
    )

    tenant_id = uuid4()
    project_area_id = uuid4()
    parent_element_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionAreaElementRepository(
        FakeSession()
    )

    result = await repository.get_active_parent_wall(
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        parent_element_id=parent_element_id,
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(project_area_id).upper() in sql
    assert str(parent_element_id).upper() in sql

    assert (
        "CONSTRUCTION_AREA_ELEMENTS.TENANT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.PROJECT_AREA_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.ELEMENT_TYPE "
        "= 'WALL'"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.DELETED_AT "
        "IS NULL"
        in sql
    )



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_006_element_creation_rejects_terminal_project(
    project_status,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status=project_status,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE LOCKED "
                "AFTER TERMINAL PROJECT CHECK"
            )

    class FakeElementRepository:
        async def create_element(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "ELEMENT MUST NOT BE CREATED "
                "IN TERMINAL PROJECT"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED ELEMENT CREATION "
                "MUST NOT BE AUDITED"
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError,
    ):
        await service.create_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            parent_element_id=None,
            element_type="WALL",
            name="Wall",
            sort_order=None,
            geometry_json={
                "schema_version": 1,
            },
            trace_id=(
                "trace-con-006-terminal-create"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_006_element_creation_requires_confirmed_area_reopening():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementAreaConfirmedError,
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="confirmed",
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def create_element(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "ELEMENT MUST NOT BE CREATED "
                "IN CONFIRMED AREA"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED ELEMENT CREATION "
                "MUST NOT BE AUDITED"
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionAreaElementAreaConfirmedError,
    ):
        await service.create_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            parent_element_id=None,
            element_type="WALL",
            name="Wall",
            sort_order=None,
            geometry_json={
                "schema_version": 1,
            },
            trace_id=(
                "trace-con-006-confirmed-create"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_006_element_service_creates_scoped_child_for_active_parent_wall():
    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    parent_element_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    parent_wall = SimpleNamespace(
        id=parent_element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        deleted_at=None,
    )
    geometry_json = {
        "schema_version": 1,
        "width": "1.25",
        "future_data": {
            "preserved": True,
        },
    }
    expected = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        parent_element_id=parent_element_id,
        element_type="WINDOW",
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(self, **kwargs):
            calls.append(("get_area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_parent_wall(
            self,
            **kwargs,
        ):
            calls.append(("get_parent", kwargs))
            return parent_wall

        async def create_element(self, **kwargs):
            calls.append(("create", kwargs))
            return expected

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.create_element(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        project_area_id=project_area_id,
        parent_element_id=parent_element_id,
        element_type=" window ",
        name="  Main window  ",
        sort_order=None,
        geometry_json=geometry_json,
        trace_id="trace-con-006-create",
    )

    assert result is expected
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "get_area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "get_parent",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "parent_element_id": (
                    parent_element_id
                ),
            },
        ),
        (
            "create",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "parent_element_id": (
                    parent_element_id
                ),
                "element_type": "WINDOW",
                "name": "Main window",
                "sort_order": None,
                "geometry_json": geometry_json,
                "created_by": user_id,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_area_element_created"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_area_element"
                ),
                "entity_id": element_id,
                "payload": {
                    "operation": "create",
                    "changes": [],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-006-create"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]



@pytest.mark.asyncio
async def test_con_006_repository_lists_active_elements_in_confirmed_deterministic_order():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionAreaElementRepository,
    )

    tenant_id = uuid4()
    project_area_id = uuid4()
    expected = [
        object(),
        object(),
    ]
    statements = []

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionAreaElementRepository(
        FakeSession()
    )

    result = await repository.list_active_elements(
        tenant_id=tenant_id,
        project_area_id=project_area_id,
    )

    assert result == expected
    assert len(statements) == 1

    sql = " ".join(
        str(
            statements[0].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={
                    "literal_binds": True,
                },
            )
        )
        .upper()
        .split()
    )

    assert str(tenant_id).upper() in sql
    assert str(project_area_id).upper() in sql

    assert (
        "CONSTRUCTION_AREA_ELEMENTS.TENANT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.PROJECT_AREA_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.DELETED_AT "
        "IS NULL"
        in sql
    )
    assert (
        "ORDER BY "
        "CONSTRUCTION_AREA_ELEMENTS.SORT_ORDER "
        "ASC NULLS LAST, "
        "CONSTRUCTION_AREA_ELEMENTS.CREATED_AT "
        "ASC, "
        "CONSTRUCTION_AREA_ELEMENTS.ID ASC"
        in sql
    )



@pytest.mark.asyncio
async def test_con_006_element_service_lists_active_elements_in_verified_project_scope():
    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OBSERVER",),
        permissions=(
            "construction.projects.read",
        ),
        grants=(
            ConstructionGrantContext(
                role="OBSERVER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        deleted_at=None,
    )
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeAreaRepository:
        async def get_active_area(self, **kwargs):
            calls.append(("get_area", kwargs))
            return area

    class FakeElementRepository:
        async def list_active_elements(
            self,
            **kwargs,
        ):
            calls.append(("list", kwargs))
            return expected

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=object(),
    )

    result = await service.list_elements(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        project_area_id=project_area_id,
    )

    assert result == expected
    assert calls == [
        (
            "get_area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "list",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
            },
        ),
    ]



@pytest.mark.asyncio
async def test_con_006_repository_gets_active_element_in_full_area_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionAreaElementRepository,
    )

    tenant_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionAreaElementRepository(
        FakeSession()
    )

    result = await repository.get_active_element(
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_id=element_id,
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(project_area_id).upper() in sql
    assert str(element_id).upper() in sql

    assert (
        "CONSTRUCTION_AREA_ELEMENTS.TENANT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.PROJECT_AREA_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.DELETED_AT "
        "IS NULL"
        in sql
    )
    assert "FOR UPDATE" not in sql



@pytest.mark.asyncio
async def test_con_006_element_service_reads_active_element_in_verified_project_scope():
    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OBSERVER",),
        permissions=(
            "construction.projects.read",
        ),
        grants=(
            ConstructionGrantContext(
                role="OBSERVER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        deleted_at=None,
    )
    expected = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeAreaRepository:
        async def get_active_area(self, **kwargs):
            calls.append(("get_area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element(
            self,
            **kwargs,
        ):
            calls.append(("get_element", kwargs))
            return expected

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=object(),
    )

    result = await service.get_element(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        project_area_id=project_area_id,
        element_id=element_id,
    )

    assert result is expected
    assert calls == [
        (
            "get_area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "get_element",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "element_id": element_id,
            },
        ),
    ]



@pytest.mark.asyncio
async def test_con_006_repository_locks_active_element_in_full_area_scope():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionAreaElementRepository,
    )

    tenant_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    expected = object()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionAreaElementRepository(
        FakeSession()
    )

    result = (
        await repository
        .get_active_element_for_update(
            tenant_id=tenant_id,
            project_area_id=project_area_id,
            element_id=element_id,
        )
    )

    assert result is expected
    assert len(statements) == 1

    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).upper()

    assert str(tenant_id).upper() in sql
    assert str(project_area_id).upper() in sql
    assert str(element_id).upper() in sql

    assert (
        "CONSTRUCTION_AREA_ELEMENTS.TENANT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.PROJECT_AREA_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.DELETED_AT "
        "IS NULL"
        in sql
    )
    assert "FOR UPDATE" in sql



@pytest.mark.asyncio
async def test_con_006_repository_updates_locked_element_fields_without_internal_commit():
    from database.repositories.construction import (
        ConstructionAreaElementRepository,
    )

    new_parent_element_id = uuid4()
    geometry_json = {
        "schema_version": 1,
        "length": "6.00",
        "height": "2.70",
        "future_data": {
            "preserved": True,
        },
    }
    calls = []
    element = SimpleNamespace(
        name="Old name",
        sort_order=None,
        geometry_json={
            "schema_version": 1,
        },
        parent_element_id=None,
    )

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            calls.append(("commit", {}))

    repository = ConstructionAreaElementRepository(
        FakeSession()
    )

    result = await repository.update_element(
        element=element,
        name="Wall A",
        sort_order=10,
        geometry_json=geometry_json,
        parent_element_id=new_parent_element_id,
    )

    assert result is element
    assert element.name == "Wall A"
    assert element.sort_order == 10
    assert element.geometry_json is geometry_json
    assert (
        element.parent_element_id
        == new_parent_element_id
    )
    assert calls == [
        ("flush", {}),
    ]



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_006_element_update_rejects_terminal_project(
    project_status,
):
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status=project_status,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "AREA MUST NOT BE LOCKED "
                "AFTER TERMINAL PROJECT CHECK"
            )

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "ELEMENT MUST NOT BE LOCKED "
                "IN TERMINAL PROJECT"
            )

        async def update_element(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "ELEMENT MUST NOT BE UPDATED "
                "IN TERMINAL PROJECT"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED ELEMENT UPDATE "
                "MUST NOT BE AUDITED"
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError,
    ):
        await service.update_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=1,
            parent_element_id=None,
            name="Updated wall",
            sort_order=None,
            geometry_json={
                "schema_version": 1,
            },
            trace_id=(
                "trace-con-006-terminal-update"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_006_element_update_requires_confirmed_area_reopening():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementAreaConfirmedError,
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="confirmed",
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "ELEMENT MUST NOT BE LOCKED "
                "IN CONFIRMED AREA"
            )

        async def update_element(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "ELEMENT MUST NOT BE UPDATED "
                "IN CONFIRMED AREA"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "REJECTED ELEMENT UPDATE "
                "MUST NOT BE AUDITED"
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionAreaElementAreaConfirmedError,
    ):
        await service.update_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=1,
            parent_element_id=None,
            name="Updated wall",
            sort_order=None,
            geometry_json={
                "schema_version": 1,
            },
            trace_id=(
                "trace-con-006-confirmed-update"
            ),
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_006_element_update_rejects_stale_row_version_before_mutation():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_foundation import (
        ConstructionRowVersionConflictError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    element = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        parent_element_id=None,
        name="Wall",
        sort_order=None,
        geometry_json={
            "schema_version": 1,
        },
        deleted_at=None,
        row_version=5,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            calls.append(("element", kwargs))
            return element

        async def update_element(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE ELEMENT MUST NOT "
                "BE UPDATED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE ELEMENT UPDATE MUST "
                "NOT BE AUDITED"
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionRowVersionConflictError,
    ) as exc_info:
        await service.update_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=4,
            parent_element_id=None,
            name="Updated wall",
            sort_order=None,
            geometry_json={
                "schema_version": 1,
            },
            trace_id=(
                "trace-con-006-stale-update"
            ),
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.code
        == "ROW_VERSION_CONFLICT"
    )
    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "area",
        "element",
        "rollback",
    ]


@pytest.mark.asyncio
async def test_con_006_element_update_noop_preserves_row_version_without_audit():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    geometry_json = {
        "schema_version": 1,
        "width": "3.50",
    }
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    element = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        parent_element_id=None,
        name="Wall",
        sort_order=None,
        geometry_json=geometry_json,
        deleted_at=None,
        row_version=4,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            calls.append(("element", kwargs))
            return element

        async def update_element(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "NO-OP ELEMENT MUST NOT "
                "BE UPDATED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "NO-OP ELEMENT MUST NOT "
                "BE AUDITED"
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=(
            FakeProjectRepository()
        ),
        event_repository=FakeEventRepository(),
    )

    result = await service.update_element(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        project_area_id=project_area_id,
        element_id=element_id,
        expected_row_version=4,
        parent_element_id=None,
        name="Wall",
        sort_order=None,
        geometry_json={
            "schema_version": 1,
            "width": "3.50",
        },
        trace_id="trace-con-006-noop-update",
    )

    assert result is element
    assert result.row_version == 4
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "element",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "element_id": element_id,
            },
        ),
        (
            "commit",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_con_006_element_service_updates_locked_element_and_reassigns_active_parent_wall():
    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    parent_element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    element = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WINDOW",
        name="Old window",
        sort_order=10,
        geometry_json={
            "schema_version": 1,
            "width": "1.00",
        },
        parent_element_id=None,
        deleted_at=None,
        row_version=1,
    )
    parent_wall = SimpleNamespace(
        id=parent_element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        deleted_at=None,
    )
    geometry_json = {
        "schema_version": 1,
        "width": "1.50",
        "height": "1.20",
        "future_data": {
            "preserved": True,
        },
    }

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(self, **kwargs):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(self, **kwargs):
            calls.append(("get_area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get_element", kwargs))
            return element

        async def get_active_parent_wall(
            self,
            **kwargs,
        ):
            calls.append(("get_parent", kwargs))
            return parent_wall

        async def update_element(self, **kwargs):
            calls.append(("update", kwargs))
            target = kwargs["element"]
            target.name = kwargs["name"]
            target.sort_order = kwargs["sort_order"]
            target.geometry_json = (
                kwargs["geometry_json"]
            )
            target.parent_element_id = (
                kwargs["parent_element_id"]
            )
            target.row_version += 1
            return target

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.update_element(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        project_area_id=project_area_id,
        element_id=element_id,
        expected_row_version=1,
        parent_element_id=parent_element_id,
        name="  Main window  ",
        sort_order=None,
        geometry_json=geometry_json,
        trace_id="trace-con-006-update",
    )

    assert result is element
    assert result.row_version == 2
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "get_area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "get_element",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "element_id": element_id,
            },
        ),
        (
            "get_parent",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "parent_element_id": (
                    parent_element_id
                ),
            },
        ),
        (
            "update",
            {
                "element": element,
                "name": "Main window",
                "sort_order": None,
                "geometry_json": geometry_json,
                "parent_element_id": (
                    parent_element_id
                ),
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_area_element_updated"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_area_element"
                ),
                "entity_id": element_id,
                "payload": {
                    "operation": "update",
                    "changes": [
                        {
                            "field": "name",
                            "old_value": (
                                "Old window"
                            ),
                            "new_value": (
                                "Main window"
                            ),
                        },
                        {
                            "field": "sort_order",
                            "old_value": 10,
                            "new_value": None,
                        },
                        {
                            "field": (
                                "geometry_json"
                            ),
                            "old_value": {
                                "schema_version": 1,
                                "width": "1.00",
                            },
                            "new_value": (
                                geometry_json
                            ),
                        },
                        {
                            "field": (
                                "parent_element_id"
                            ),
                            "old_value": None,
                            "new_value": str(
                                parent_element_id
                            ),
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-006-update"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]



@pytest.mark.asyncio
async def test_con_006_repository_detects_active_children_before_wall_archive():
    from sqlalchemy.dialects import postgresql

    from database.repositories.construction import (
        ConstructionAreaElementRepository,
    )

    tenant_id = uuid4()
    project_area_id = uuid4()
    wall_element_id = uuid4()
    statements = []

    class FakeResult:
        def scalar_one_or_none(self):
            return uuid4()

    class FakeSession:
        async def execute(self, statement):
            statements.append(statement)
            return FakeResult()

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionAreaElementRepository(
        FakeSession()
    )

    result = (
        await repository
        .has_active_wall_children(
            tenant_id=tenant_id,
            project_area_id=project_area_id,
            wall_element_id=wall_element_id,
        )
    )

    assert result is True
    assert len(statements) == 1

    sql = " ".join(
        str(
            statements[0].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={
                    "literal_binds": True,
                },
            )
        )
        .upper()
        .split()
    )

    assert str(tenant_id).upper() in sql
    assert str(project_area_id).upper() in sql
    assert str(wall_element_id).upper() in sql

    assert (
        "CONSTRUCTION_AREA_ELEMENTS.TENANT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.PROJECT_AREA_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.PARENT_ELEMENT_ID"
        in sql
    )
    assert (
        "CONSTRUCTION_AREA_ELEMENTS.DELETED_AT "
        "IS NULL"
        in sql
    )

    for element_type in (
        "WINDOW",
        "DOOR",
        "OPENING",
    ):
        assert f"'{element_type}'" in sql



@pytest.mark.asyncio
async def test_con_006_repository_archives_locked_element_without_physical_delete():
    from datetime import UTC, datetime

    from database.repositories.construction import (
        ConstructionAreaElementRepository,
    )

    deleted_at = datetime(
        2026,
        9,
        18,
        18,
        30,
        tzinfo=UTC,
    )
    calls = []
    element = SimpleNamespace(
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def delete(self, value):
            calls.append(("delete", value))

        async def commit(self):
            calls.append(("commit", {}))

    repository = ConstructionAreaElementRepository(
        FakeSession()
    )

    result = await repository.soft_delete_element(
        element=element,
        deleted_at=deleted_at,
    )

    assert result is element
    assert element.deleted_at == deleted_at
    assert element.row_version == 2
    assert calls == [
        ("flush", {}),
    ]




@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_status",
    (
        "completed",
        "cancelled",
    ),
)
async def test_con_006_element_archive_rejects_terminal_project(
    project_status,
):
    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_foundation import (
        ConstructionProjectTerminalError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return SimpleNamespace(
                id=project_id,
                tenant_id=tenant_id,
                status=project_status,
                deleted_at=None,
            )

    class FakeAreaRepository:
        async def get_active_area(self, **kwargs):
            raise AssertionError(
                "Area must not be read for terminal project."
            )

        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Area must not be locked for terminal project."
            )

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Element must not be locked for terminal project."
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
    )

    with pytest.raises(
        ConstructionProjectTerminalError
    ):
        await service.archive_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=1,
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        ("rollback", {}),
    ]



@pytest.mark.asyncio
async def test_con_006_element_archive_requires_confirmed_area_reopening():
    from services.construction_elements import (
        ConstructionAreaElementAreaConfirmedError,
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return SimpleNamespace(
                id=project_id,
                tenant_id=tenant_id,
                status="active",
                deleted_at=None,
            )

    class FakeAreaRepository:
        async def get_active_area(self, **kwargs):
            calls.append(("area", kwargs))
            return SimpleNamespace(
                id=project_area_id,
                tenant_id=tenant_id,
                project_id=project_id,
                status="confirmed",
                deleted_at=None,
            )

        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return SimpleNamespace(
                id=project_area_id,
                tenant_id=tenant_id,
                project_id=project_id,
                status="confirmed",
                deleted_at=None,
            )

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Element must not be locked before area reopening."
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
    )

    with pytest.raises(
        ConstructionAreaElementAreaConfirmedError
    ):
        await service.archive_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=1,
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        ("rollback", {}),
    ]



@pytest.mark.asyncio
async def test_con_006_element_archive_rejects_stale_row_version_before_mutation():
    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_foundation import (
        ConstructionRowVersionConflictError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    element = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        deleted_at=None,
        row_version=5,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            calls.append(("element", kwargs))
            return element

        async def has_active_wall_children(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE ELEMENT MUST NOT CHECK CHILDREN"
            )

        async def soft_delete_element(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE ELEMENT MUST NOT BE ARCHIVED"
            )

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "STALE ELEMENT ARCHIVE MUST NOT BE AUDITED"
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
    )

    with pytest.raises(
        ConstructionRowVersionConflictError
    ) as exc_info:
        await service.archive_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=4,
            trace_id="trace-con-006-stale-archive",
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.code
        == "ROW_VERSION_CONFLICT"
    )
    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "area",
        "element",
        "rollback",
    ]


@pytest.mark.asyncio
async def test_con_006_element_service_rejects_wall_archive_with_active_children():
    from datetime import UTC, datetime

    from services.construction_elements import (
        ConstructionAreaElementHasActiveChildrenError,
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    now = datetime(
        2026,
        9,
        18,
        19,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    wall = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get_element", kwargs))
            return wall

        async def has_active_wall_children(
            self,
            **kwargs,
        ):
            calls.append(("has_children", kwargs))
            return True

        async def soft_delete_element(
            self,
            **kwargs,
        ):
            calls.append(("archive", kwargs))
            return kwargs["element"]

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        now_provider=lambda: now,
    )

    with pytest.raises(
        ConstructionAreaElementHasActiveChildrenError
    ):
        await service.archive_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=1,
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "get_element",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "element_id": element_id,
            },
        ),
        (
            "has_children",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "wall_element_id": element_id,
            },
        ),
        (
            "rollback",
            {},
        ),
    ]



@pytest.mark.asyncio
async def test_con_006_element_service_archives_wall_without_active_children_atomically():
    from datetime import UTC, datetime

    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    deleted_at = datetime(
        2026,
        9,
        18,
        19,
        30,
        tzinfo=UTC,
    )
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    wall = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            calls.append(("get_element", kwargs))
            return wall

        async def has_active_wall_children(
            self,
            **kwargs,
        ):
            calls.append(("has_children", kwargs))
            return False

        async def soft_delete_element(
            self,
            **kwargs,
        ):
            calls.append(("archive", kwargs))
            kwargs["element"].deleted_at = (
                kwargs["deleted_at"]
            )
            kwargs["element"].row_version += 1
            return kwargs["element"]

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FakeEventRepository(),
        now_provider=lambda: deleted_at,
    )

    result = await service.archive_element(
        actor=actor,
        access_context=access_context,
        project_id=project_id,
        project_area_id=project_area_id,
        element_id=element_id,
        expected_row_version=1,
        trace_id="trace-con-006-archive",
    )

    assert result is wall
    assert result.deleted_at == deleted_at
    assert result.row_version == 2
    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
        (
            "area",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "area_id": project_area_id,
            },
        ),
        (
            "get_element",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "element_id": element_id,
            },
        ),
        (
            "has_children",
            {
                "tenant_id": tenant_id,
                "project_area_id": (
                    project_area_id
                ),
                "wall_element_id": element_id,
            },
        ),
        (
            "archive",
            {
                "element": wall,
                "deleted_at": deleted_at,
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_area_element_archived"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_area_element"
                ),
                "entity_id": element_id,
                "payload": {
                    "operation": "archive",
                    "changes": [
                        {
                            "field": "deleted_at",
                            "old_value": None,
                            "new_value": (
                                deleted_at.isoformat()
                            ),
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-006-archive"
                ),
            },
        ),
        (
            "commit",
            {},
        ),
    ]



def test_con_007_audit_event_registry_matches_confirmed_contract():
    from services.construction_audit import (
        CONSTRUCTION_AUDIT_EVENT_TYPES,
    )

    assert (
        CONSTRUCTION_AUDIT_EVENT_TYPES
        == frozenset(
            {
                "construction_access_grant_created",
                "construction_access_grant_revoked",
                "construction_client_created",
                "construction_client_updated",
                "construction_client_archived",
                "construction_project_created",
                "construction_project_updated",
                "construction_project_status_changed",
                "construction_project_area_created",
                "construction_project_area_updated",
                "construction_project_area_status_changed",
                "construction_project_area_reopened",
                "construction_project_area_archived",
                "construction_area_element_created",
                "construction_area_element_updated",
                "construction_area_element_archived",
            }
        )
    )



@pytest.mark.asyncio
async def test_con_007_audit_logger_records_one_platform_event_per_atomic_operation():
    from services.construction_audit import (
        ConstructionAuditChange,
        ConstructionAuditLogger,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    entity_id = uuid4()
    calls = []
    expected = SimpleNamespace(id=uuid4())

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )

    class FakeEventRepository:
        async def create_event(self, **kwargs):
            calls.append(("event", kwargs))
            return expected

    logger = ConstructionAuditLogger(
        event_repository=FakeEventRepository()
    )

    result = await logger.record_operation(
        actor=actor,
        event_type=(
            "construction_project_status_changed"
        ),
        entity_type="construction_project",
        entity_id=entity_id,
        operation="status_change",
        changes=(
            ConstructionAuditChange(
                field="status",
                old_value="draft",
                new_value="planning",
            ),
            ConstructionAuditChange(
                field="row_version",
                old_value=1,
                new_value=2,
            ),
        ),
        reason="Planning started",
        trace_id="trace-con-007",
    )

    assert result is expected
    assert calls == [
        (
            "event",
            {
                "event_type": (
                    "construction_project_"
                    "status_changed"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_project"
                ),
                "entity_id": entity_id,
                "payload": {
                    "operation": (
                        "status_change"
                    ),
                    "changes": [
                        {
                            "field": "status",
                            "old_value": "draft",
                            "new_value": (
                                "planning"
                            ),
                        },
                        {
                            "field": (
                                "row_version"
                            ),
                            "old_value": 1,
                            "new_value": 2,
                        },
                    ],
                    "reason": (
                        "Planning started"
                    ),
                },
                "platform": "api",
                "trace_id": "trace-con-007",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_con_003_repository_updates_locked_client_without_internal_commit():
    from types import SimpleNamespace
    from uuid import uuid4

    from database.repositories.construction import (
        ConstructionClientRepository,
    )

    client = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        display_name="Old name",
        client_type="person",
        phone=None,
        email=None,
        notes=None,
    )
    calls = []

    class FakeSession:
        async def flush(self):
            calls.append(("flush", {}))

        async def commit(self):
            raise AssertionError(
                "Repository must not commit."
            )

    repository = ConstructionClientRepository(
        FakeSession()
    )

    result = await repository.update_client(
        client=client,
        display_name="Updated Company",
        client_type="company",
        phone="+351920000000",
        email="office@example.com",
        notes="Updated notes",
    )

    assert result is client
    assert client.display_name == "Updated Company"
    assert client.client_type == "company"
    assert client.phone == "+351920000000"
    assert client.email == "office@example.com"
    assert client.notes == "Updated notes"
    assert calls == [
        ("flush", {}),
    ]


@pytest.mark.asyncio
async def test_con_003_client_service_updates_locked_client_and_audits_atomically():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_clients import (
        ConstructionClientService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    client_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    client = SimpleNamespace(
        id=client_id,
        tenant_id=tenant_id,
        display_name="Old name",
        client_type="person",
        phone=None,
        email=None,
        notes=None,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_client_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return client

        async def update_client(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            target = kwargs["client"]
            target.display_name = (
                kwargs["display_name"]
            )
            target.client_type = (
                kwargs["client_type"]
            )
            target.phone = kwargs["phone"]
            target.email = kwargs["email"]
            target.notes = kwargs["notes"]
            return target

    class FakeEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))

    service = ConstructionClientService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FakeEventRepository(),
    )

    result = await service.update_client(
        actor=actor,
        access_context=access_context,
        client_id=client_id,
        display_name="Updated Company",
        client_type="company",
        phone="+351920000000",
        email="office@example.com",
        notes="Updated notes",
        trace_id="trace-con-003-update",
    )

    assert result is client
    assert calls == [
        (
            "lock",
            {
                "tenant_id": tenant_id,
                "client_id": client_id,
            },
        ),
        (
            "update",
            {
                "client": client,
                "display_name": (
                    "Updated Company"
                ),
                "client_type": "company",
                "phone": "+351920000000",
                "email": "office@example.com",
                "notes": "Updated notes",
            },
        ),
        (
            "event",
            {
                "event_type": (
                    "construction_client_updated"
                ),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "entity_type": (
                    "construction_client"
                ),
                "entity_id": client_id,
                "payload": {
                    "operation": "update",
                    "changes": [
                        {
                            "field": "display_name",
                            "old_value": "Old name",
                            "new_value": (
                                "Updated Company"
                            ),
                        },
                        {
                            "field": "client_type",
                            "old_value": "person",
                            "new_value": "company",
                        },
                        {
                            "field": "phone",
                            "old_value": None,
                            "new_value": (
                                "+351920000000"
                            ),
                        },
                        {
                            "field": "email",
                            "old_value": None,
                            "new_value": (
                                "office@example.com"
                            ),
                        },
                        {
                            "field": "notes",
                            "old_value": None,
                            "new_value": (
                                "Updated notes"
                            ),
                        },
                    ],
                    "reason": None,
                },
                "platform": "api",
                "trace_id": (
                    "trace-con-003-update"
                ),
            },
        ),
        ("commit", {}),
    ]


@pytest.mark.asyncio
async def test_con_007_required_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_clients import (
        ConstructionClientOperationError,
        ConstructionClientService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    client_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def create_client(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return SimpleNamespace(
                id=client_id,
                tenant_id=tenant_id,
            )

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionClientService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=(
            FailingEventRepository()
        ),
    )

    with pytest.raises(
        ConstructionClientOperationError,
    ):
        await service.create_client(
            actor=actor,
            access_context=access_context,
            display_name="Audit failure",
            client_type="person",
            phone=None,
            email=None,
            notes=None,
            trace_id=(
                "trace-con-007-failure"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "create",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )



@pytest.mark.asyncio
async def test_con_007_client_update_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_clients import (
        ConstructionClientOperationError,
        ConstructionClientService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    client_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    client = SimpleNamespace(
        id=client_id,
        tenant_id=tenant_id,
        display_name="Old name",
        client_type="person",
        phone=None,
        email=None,
        notes=None,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_client_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return client

        async def update_client(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            target = kwargs["client"]
            target.display_name = kwargs["display_name"]
            target.client_type = kwargs["client_type"]
            target.phone = kwargs["phone"]
            target.email = kwargs["email"]
            target.notes = kwargs["notes"]
            return target

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionClientService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=(
            FailingEventRepository()
        ),
    )

    with pytest.raises(
        ConstructionClientOperationError,
    ):
        await service.update_client(
            actor=actor,
            access_context=access_context,
            client_id=client_id,
            display_name="Updated name",
            client_type="company",
            phone="+351920000000",
            email="office@example.com",
            notes="Updated notes",
            trace_id=(
                "trace-con-007-client-update"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "update",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_client_archive_audit_failure_rolls_back_domain_change():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_clients import (
        ConstructionClientOperationError,
        ConstructionClientService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    client_id = uuid4()
    deleted_at = datetime(
        2026,
        9,
        21,
        12,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    client = SimpleNamespace(
        id=client_id,
        tenant_id=tenant_id,
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_client_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return client

        async def soft_delete_client(
            self,
            **kwargs,
        ):
            calls.append(("archive", kwargs))
            kwargs["client"].deleted_at = (
                kwargs["deleted_at"]
            )
            return kwargs["client"]

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionClientService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=(
            FailingEventRepository()
        ),
        now_provider=lambda: deleted_at,
    )

    with pytest.raises(
        ConstructionClientOperationError,
    ):
        await service.archive_client(
            actor=actor,
            access_context=access_context,
            client_id=client_id,
            trace_id=(
                "trace-con-007-client-archive"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "archive",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_project_status_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectOperationError,
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="draft",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return project

        async def set_project_status(
            self,
            **kwargs,
        ):
            calls.append(("status", kwargs))
            kwargs["project"].status = (
                kwargs["status"]
            )
            kwargs["project"].row_version += 1
            return kwargs["project"]

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=(
            FailingEventRepository()
        ),
    )

    with pytest.raises(
        ConstructionProjectOperationError,
    ):
        await service.transition_project_status(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            next_status="planning",
            expected_row_version=1,
            trace_id=(
                "trace-con-007-project-status"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "lock",
        "status",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_area_rename_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaOperationError,
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        name="Old area",
        status="draft",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def rename_area(
            self,
            **kwargs,
        ):
            calls.append(("rename", kwargs))
            kwargs["area"].name = kwargs["name"]
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=(
            FailingEventRepository()
        ),
    )

    with pytest.raises(
        ConstructionProjectAreaOperationError,
    ):
        await service.rename_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            name="Updated area",
            trace_id=(
                "trace-con-007-area-rename"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "rename",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_area_sort_order_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaOperationError,
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        sort_order=5,
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def set_area_sort_order(
            self,
            **kwargs,
        ):
            calls.append(("sort", kwargs))
            kwargs["area"].sort_order = (
                kwargs["sort_order"]
            )
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=(
            FailingEventRepository()
        ),
    )

    with pytest.raises(
        ConstructionProjectAreaOperationError,
    ):
        await service.update_area_sort_order(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            sort_order=None,
            trace_id=(
                "trace-con-007-area-sort"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "sort",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_area_status_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaOperationError,
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def set_area_status(
            self,
            **kwargs,
        ):
            calls.append(("status", kwargs))
            kwargs["area"].status = (
                kwargs["status"]
            )
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=(
            FailingEventRepository()
        ),
    )

    with pytest.raises(
        ConstructionProjectAreaOperationError,
    ):
        await service.transition_area_status(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            next_status="measured",
            trace_id=(
                "trace-con-007-area-status"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "status",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_area_reopen_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaOperationError,
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="confirmed",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def set_area_status(
            self,
            **kwargs,
        ):
            calls.append(("reopen", kwargs))
            kwargs["area"].status = (
                kwargs["status"]
            )
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=(
            FailingEventRepository()
        ),
    )

    with pytest.raises(
        ConstructionProjectAreaOperationError,
    ):
        await service.reopen_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            trace_id=(
                "trace-con-007-area-reopen"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "reopen",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_area_archive_audit_failure_rolls_back_domain_change():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaOperationError,
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    deleted_at = datetime(
        2026,
        9,
        21,
        13,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return area

        async def has_active_elements(
            self,
            **kwargs,
        ):
            calls.append(
                ("active_elements", kwargs)
            )
            return False

        async def soft_delete_area(
            self,
            **kwargs,
        ):
            calls.append(("archive", kwargs))
            kwargs["area"].deleted_at = (
                kwargs["deleted_at"]
            )
            kwargs["area"].row_version += 1
            return kwargs["area"]

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=(
            FailingEventRepository()
        ),
        now_provider=lambda: deleted_at,
    )

    with pytest.raises(
        ConstructionProjectAreaOperationError,
    ):
        await service.archive_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            trace_id=(
                "trace-con-007-area-archive"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "lock",
        "active_elements",
        "archive",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_element_update_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementOperationError,
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    parent_element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    element = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WINDOW",
        name="Old window",
        sort_order=10,
        geometry_json={
            "schema_version": 1,
            "width": "1.00",
        },
        parent_element_id=None,
        deleted_at=None,
        row_version=1,
    )
    parent_wall = SimpleNamespace(
        id=parent_element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        deleted_at=None,
    )
    geometry_json = {
        "schema_version": 1,
        "width": "1.50",
    }

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(self, **kwargs):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            calls.append(("element", kwargs))
            return element

        async def get_active_parent_wall(
            self,
            **kwargs,
        ):
            calls.append(("parent", kwargs))
            return parent_wall

        async def update_element(
            self,
            **kwargs,
        ):
            calls.append(("update", kwargs))
            target = kwargs["element"]
            target.name = kwargs["name"]
            target.sort_order = kwargs["sort_order"]
            target.geometry_json = (
                kwargs["geometry_json"]
            )
            target.parent_element_id = (
                kwargs["parent_element_id"]
            )
            target.row_version += 1
            return target

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=(
            FailingEventRepository()
        ),
    )

    with pytest.raises(
        ConstructionAreaElementOperationError,
    ):
        await service.update_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=1,
            parent_element_id=parent_element_id,
            name="Updated window",
            sort_order=None,
            geometry_json=geometry_json,
            trace_id=(
                "trace-con-007-element-update"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "area",
        "element",
        "parent",
        "update",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_element_archive_audit_failure_rolls_back_domain_change():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementOperationError,
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    project_area_id = uuid4()
    element_id = uuid4()
    deleted_at = datetime(
        2026,
        9,
        21,
        14,
        0,
        tzinfo=UTC,
    )
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("PROJECT_MANAGER",),
        permissions=(
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=project_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=project_area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )
    wall = SimpleNamespace(
        id=element_id,
        tenant_id=tenant_id,
        project_area_id=project_area_id,
        element_type="WALL",
        deleted_at=None,
        row_version=1,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def get_active_element_for_update(
            self,
            **kwargs,
        ):
            calls.append(("element", kwargs))
            return wall

        async def has_active_wall_children(
            self,
            **kwargs,
        ):
            calls.append(("children", kwargs))
            return False

        async def soft_delete_element(
            self,
            **kwargs,
        ):
            calls.append(("archive", kwargs))
            kwargs["element"].deleted_at = (
                kwargs["deleted_at"]
            )
            kwargs["element"].row_version += 1
            return kwargs["element"]

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=(
            FailingEventRepository()
        ),
        now_provider=lambda: deleted_at,
    )

    with pytest.raises(
        ConstructionAreaElementOperationError,
    ):
        await service.archive_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=project_area_id,
            element_id=element_id,
            expected_row_version=1,
            trace_id=(
                "trace-con-007-element-archive"
            ),
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "area",
        "element",
        "children",
        "archive",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )


@pytest.mark.asyncio
async def test_con_007_access_grant_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_grants import (
        ConstructionAccessGrantService,
        ConstructionGrantOperationError,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    target_user_id = uuid4()
    grant_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=actor_user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.access.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    grant = SimpleNamespace(
        id=grant_id,
        tenant_id=tenant_id,
        user_id=target_user_id,
        role="ADMIN",
        scope_type="tenant",
        scope_id=tenant_id,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            calls.append(("membership", kwargs))
            return True

        async def create_grant(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return grant

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionAccessGrantService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FailingEventRepository(),
    )

    with pytest.raises(
        ConstructionGrantOperationError,
    ):
        await service.grant_access(
            actor=actor,
            access_context=access_context,
            target_user_id=target_user_id,
            role="ADMIN",
            scope_type="tenant",
            scope_id=tenant_id,
            trace_id="trace-con-007-grant-failure",
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "membership",
        "create",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )



@pytest.mark.asyncio
async def test_con_007_project_create_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction_projects import (
        ConstructionProjectOperationError,
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def create_project(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return SimpleNamespace(
                id=project_id,
                tenant_id=tenant_id,
                status="draft",
            )

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionProjectService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=FailingEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectOperationError,
    ):
        await service.create_project(
            actor=actor,
            access_context=access_context,
            name="Audit failure project",
            address_raw="Rua do Teste 10",
            client_id=None,
            responsible_user_id=None,
            comment=None,
            trace_id="trace-con-007-project-failure",
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "create",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )



@pytest.mark.asyncio
async def test_con_007_project_area_create_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaOperationError,
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def create_area(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return SimpleNamespace(
                id=area_id,
                tenant_id=tenant_id,
                project_id=project_id,
                status="draft",
            )

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionProjectAreaService(
        session=FakeSession(),
        repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FailingEventRepository(),
    )

    with pytest.raises(
        ConstructionProjectAreaOperationError,
    ):
        await service.create_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_type="ROOM",
            name="Audit failure area",
            sort_order=None,
            trace_id="trace-con-007-area-failure",
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "create",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )



@pytest.mark.asyncio
async def test_con_007_area_element_create_audit_failure_rolls_back_domain_change():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementOperationError,
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    element_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.projects.create",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    project = SimpleNamespace(
        id=project_id,
        tenant_id=tenant_id,
        status="active",
        deleted_at=None,
    )
    area = SimpleNamespace(
        id=area_id,
        tenant_id=tenant_id,
        project_id=project_id,
        status="draft",
        deleted_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeProjectRepository:
        async def get_active_project_for_update(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return project

    class FakeAreaRepository:
        async def get_active_area_for_update(
            self,
            **kwargs,
        ):
            calls.append(("area", kwargs))
            return area

    class FakeElementRepository:
        async def create_element(
            self,
            **kwargs,
        ):
            calls.append(("create", kwargs))
            return SimpleNamespace(
                id=element_id,
                tenant_id=tenant_id,
                project_area_id=area_id,
                element_type="WALL",
            )

    class FailingEventRepository:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(("event", kwargs))
            raise RuntimeError(
                "Private audit storage details."
            )

    service = ConstructionAreaElementService(
        session=FakeSession(),
        repository=FakeElementRepository(),
        area_repository=FakeAreaRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=FailingEventRepository(),
    )

    with pytest.raises(
        ConstructionAreaElementOperationError,
    ):
        await service.create_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=area_id,
            parent_element_id=None,
            element_type="WALL",
            name="Audit failure wall",
            sort_order=None,
            geometry_json={
                "schema_version": 1,
            },
            trace_id="trace-con-007-element-failure",
        )

    assert [
        name
        for name, _ in calls
    ] == [
        "project",
        "area",
        "create",
        "event",
        "rollback",
    ]
    assert not any(
        name == "commit"
        for name, _ in calls
    )



def test_con_008_does_not_expose_deferred_stage_one_domain_crud():
    from api.routes.construction import (
        router,
    )

    deferred_resource_paths = (
        "/construction/clients",
        "/construction/projects",
        "/construction/project-areas",
        "/construction/areas",
        "/construction/area-elements",
        "/construction/elements",
    )

    registered_routes = [
        (
            method,
            route.path,
        )
        for route in router.routes
        for method in sorted(
            getattr(route, "methods", ())
        )
    ]

    forbidden_routes = [
        (
            method,
            route_path,
        )
        for method, route_path
        in registered_routes
        if any(
            route_path == deferred_path
            or route_path.startswith(
                f"{deferred_path}/"
            )
            for deferred_path
            in deferred_resource_paths
        )
    ]

    assert forbidden_routes == []



@pytest.mark.asyncio
async def test_con_008_client_and_project_services_fail_closed_without_permission():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_clients import (
        ConstructionClientService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
    )
    from services.construction_projects import (
        ConstructionProjectService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    client_id = uuid4()
    project_id = uuid4()

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    client_service = ConstructionClientService(
        session=object(),
        repository=object(),
        event_repository=object(),
    )
    project_service = ConstructionProjectService(
        session=object(),
        repository=object(),
        event_repository=object(),
    )

    operations = (
        (
            "client_create",
            lambda: client_service.create_client(
                actor=actor,
                access_context=access_context,
                display_name="Denied client",
                client_type="person",
                phone=None,
                email=None,
                notes=None,
            ),
        ),
        (
            "client_update",
            lambda: client_service.update_client(
                actor=actor,
                access_context=access_context,
                client_id=client_id,
                display_name="Denied client",
                client_type="person",
                phone=None,
                email=None,
                notes=None,
            ),
        ),
        (
            "client_archive",
            lambda: client_service.archive_client(
                actor=actor,
                access_context=access_context,
                client_id=client_id,
            ),
        ),
        (
            "project_create",
            lambda: project_service.create_project(
                actor=actor,
                access_context=access_context,
                name="Denied project",
                address_raw="Denied address",
                client_id=None,
                responsible_user_id=None,
                comment=None,
            ),
        ),
        (
            "project_status",
            lambda: (
                project_service
                .transition_project_status(
                    actor=actor,
                    access_context=access_context,
                    project_id=project_id,
                    next_status="planning",
                    expected_row_version=1,
                )
            ),
        ),
    )

    for operation_name, operation in operations:
        with pytest.raises(
            ConstructionPermissionDeniedError,
            match="permission",
        ):
            await operation()



@pytest.mark.asyncio
async def test_con_008_project_area_service_operations_fail_closed_without_permission():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_areas import (
        ConstructionProjectAreaService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    service = ConstructionProjectAreaService(
        session=object(),
        repository=object(),
        project_repository=object(),
        event_repository=object(),
    )

    operations = (
        lambda: service.create_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_type="ROOM",
            name="Denied area",
            sort_order=None,
        ),
        lambda: service.reopen_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
        ),
        lambda: service.transition_area_status(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            next_status="measured",
        ),
        lambda: service.rename_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            name="Denied rename",
        ),
        lambda: service.archive_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
        ),
        lambda: service.get_area(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
        ),
        lambda: service.list_areas(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
        ),
        lambda: service.update_area_sort_order(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            area_id=area_id,
            expected_row_version=1,
            sort_order=1,
        ),
    )

    for operation in operations:
        with pytest.raises(
            ConstructionPermissionDeniedError,
            match="permission",
        ):
            await operation()



@pytest.mark.asyncio
async def test_con_008_area_element_service_operations_fail_closed_without_permission():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_elements import (
        ConstructionAreaElementService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    area_id = uuid4()
    element_id = uuid4()

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    service = ConstructionAreaElementService(
        session=object(),
        repository=object(),
        area_repository=object(),
        project_repository=object(),
        event_repository=object(),
    )

    operations = (
        lambda: service.create_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=area_id,
            parent_element_id=None,
            element_type="WALL",
            name="Denied wall",
            sort_order=None,
            geometry_json={
                "schema_version": 1,
            },
        ),
        lambda: service.list_elements(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=area_id,
        ),
        lambda: service.get_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=area_id,
            element_id=element_id,
        ),
        lambda: service.update_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=area_id,
            element_id=element_id,
            expected_row_version=1,
            parent_element_id=None,
            name="Denied wall update",
            sort_order=None,
            geometry_json={
                "schema_version": 1,
            },
        ),
        lambda: service.archive_element(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
            project_area_id=area_id,
            element_id=element_id,
            expected_row_version=1,
        ),
    )

    for operation in operations:
        with pytest.raises(
            ConstructionPermissionDeniedError,
            match="permission",
        ):
            await operation()



@pytest.mark.asyncio
async def test_con_008_access_grant_service_operations_fail_closed_without_manage_permission():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_grants import (
        ConstructionAccessGrantService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    target_user_id = uuid4()
    grant_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=actor_user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )
    grant = SimpleNamespace(
        id=grant_id,
        tenant_id=tenant_id,
        user_id=target_user_id,
        role="ADMIN",
        scope_type="tenant",
        scope_id=tenant_id,
        status="active",
        revoked_by_user_id=None,
        revoked_at=None,
    )

    class FakeSession:
        async def commit(self):
            calls.append(("commit", {}))

        async def rollback(self):
            calls.append(("rollback", {}))

    class FakeRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Permission must be checked before "
                "grant creation."
            )

        async def get_grant_for_update(
            self,
            **kwargs,
        ):
            calls.append(("lock", kwargs))
            return grant

        async def revoke_grant(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Denied grant must not be revoked."
            )

    service = ConstructionAccessGrantService(
        session=FakeSession(),
        repository=FakeRepository(),
        event_repository=object(),
    )

    with pytest.raises(
        ConstructionPermissionDeniedError,
        match="permission",
    ):
        await service.grant_access(
            actor=actor,
            access_context=access_context,
            target_user_id=target_user_id,
            role="ADMIN",
            scope_type="tenant",
            scope_id=tenant_id,
        )

    with pytest.raises(
        ConstructionPermissionDeniedError,
        match="permission",
    ):
        await service.revoke_access(
            actor=actor,
            access_context=access_context,
            grant_id=grant_id,
        )

    assert calls == [
        (
            "lock",
            {
                "tenant_id": tenant_id,
                "grant_id": grant_id,
            },
        ),
        ("rollback", {}),
    ]





@pytest.mark.asyncio
async def test_con_002_project_scope_grant_requires_active_project_in_actor_tenant():
    from services.construction_grants import (
        ConstructionAccessGrantService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionScopeDeniedError,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    target_user_id = uuid4()
    project_id = uuid4()
    calls = []

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=actor_user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.access.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    class FakeProjectRepository:
        async def get_active_project(
            self,
            **kwargs,
        ):
            calls.append(("project", kwargs))
            return None

    class FakeGrantRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "TARGET MEMBERSHIP MUST NOT BE CHECKED "
                "FOR AN INVALID PROJECT"
            )

        async def create_grant(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "GRANT MUST NOT BE CREATED "
                "FOR AN INVALID PROJECT"
            )

    service = ConstructionAccessGrantService(
        session=object(),
        repository=FakeGrantRepository(),
        project_repository=FakeProjectRepository(),
        event_repository=object(),
    )

    with pytest.raises(
        ConstructionScopeDeniedError
    ):
        await service.grant_access(
            actor=actor,
            access_context=access_context,
            target_user_id=target_user_id,
            role="PROJECT_MANAGER",
            scope_type="project",
            scope_id=project_id,
            project_id=project_id,
        )

    assert calls == [
        (
            "project",
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_con_002_document_scope_grant_is_blocked_until_document_center():
    from services.construction_grants import (
        ConstructionAccessGrantService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionScopeDeniedError,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    target_user_id = uuid4()
    project_id = uuid4()
    document_id = uuid4()

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=actor_user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.access.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    class FakeRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "DOCUMENT GRANT MUST BE REJECTED "
                "BEFORE REPOSITORY ACCESS"
            )

        async def create_grant(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "DOCUMENT GRANT MUST NOT BE CREATED"
            )

    service = ConstructionAccessGrantService(
        session=object(),
        repository=FakeRepository(),
        event_repository=object(),
    )

    with pytest.raises(
        ConstructionScopeDeniedError
    ):
        await service.grant_access(
            actor=actor,
            access_context=access_context,
            target_user_id=target_user_id,
            role="OBSERVER",
            scope_type="document",
            scope_id=document_id,
            project_id=project_id,
        )


@pytest.mark.asyncio
async def test_con_002_tenant_scope_rejects_non_tenant_construction_roles():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_grants import (
        ConstructionAccessGrantService,
    )
    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionScopeDeniedError,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    target_user_id = uuid4()

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=actor_user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=(
            "construction.access.manage",
        ),
        grants=(
            ConstructionGrantContext(
                role="OWNER",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    class FakeRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Invalid role/scope pair must be "
                "rejected before repository access."
            )

    service = ConstructionAccessGrantService(
        session=object(),
        repository=FakeRepository(),
        event_repository=object(),
    )

    for role in (
        "SURVEYOR",
        "ESTIMATOR",
        "PROJECT_MANAGER",
        "EXECUTOR",
        "OBSERVER",
    ):
        with pytest.raises(
            ConstructionScopeDeniedError,
        ):
            await service.grant_access(
                actor=actor,
                access_context=access_context,
                target_user_id=target_user_id,
                role=role,
                scope_type="tenant",
                scope_id=tenant_id,
            )



def test_con_002_access_grant_model_restricts_tenant_scope_to_owner_and_admin():
    from sqlalchemy import CheckConstraint

    from database.models import (
        ConstructionAccessGrant,
    )

    constraints = {
        constraint.name: str(
            constraint.sqltext
        )
        for constraint
        in ConstructionAccessGrant
        .__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    constraint_name = (
        "ck_construction_access_grants_"
        "tenant_role_scope"
    )
    assert constraint_name in constraints

    sql = " ".join(
        constraints[constraint_name]
        .lower()
        .split()
    )

    assert "scope_type <> 'tenant'" in sql
    assert "role in ('owner', 'admin')" in sql



def test_con_002_permission_does_not_leak_between_project_scoped_roles():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionScopeDeniedError,
        require_construction_access,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    observed_project_id = uuid4()
    managed_project_id = uuid4()

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=(
            "OBSERVER",
            "PROJECT_MANAGER",
        ),
        permissions=(
            "construction.projects.read",
            "construction.projects.edit",
        ),
        grants=(
            ConstructionGrantContext(
                role="OBSERVER",
                scope_type="project",
                scope_id=observed_project_id,
            ),
            ConstructionGrantContext(
                role="PROJECT_MANAGER",
                scope_type="project",
                scope_id=managed_project_id,
            ),
        ),
    )

    require_construction_access(
        actor=actor,
        access_context=access_context,
        permission_code=(
            "construction.projects.read"
        ),
        project_id=observed_project_id,
    )

    with pytest.raises(
        ConstructionScopeDeniedError,
    ):
        require_construction_access(
            actor=actor,
            access_context=access_context,
            permission_code=(
                "construction.projects.edit"
            ),
            project_id=observed_project_id,
        )

    require_construction_access(
        actor=actor,
        access_context=access_context,
        permission_code=(
            "construction.projects.edit"
        ),
        project_id=managed_project_id,
    )



@pytest.mark.asyncio
async def test_con_002_admin_cannot_delegate_owner_permissions_beyond_own_policy():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction_grants import (
        ConstructionAccessGrantService,
    )
    from services.construction_permissions import (
        CONSTRUCTION_ROLE_PERMISSIONS,
        ConstructionAccessContext,
        ConstructionGrantContext,
        ConstructionPermissionDeniedError,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    target_user_id = uuid4()

    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=actor_user_id,
    )
    access_context = ConstructionAccessContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=tuple(
            sorted(
                CONSTRUCTION_ROLE_PERMISSIONS[
                    "ADMIN"
                ]
            )
        ),
        grants=(
            ConstructionGrantContext(
                role="ADMIN",
                scope_type="tenant",
                scope_id=tenant_id,
            ),
        ),
    )

    class FakeRepository:
        async def has_active_platform_membership(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Privilege escalation must be "
                "rejected before repository access."
            )

    service = ConstructionAccessGrantService(
        session=object(),
        repository=FakeRepository(),
        event_repository=object(),
    )

    with pytest.raises(
        ConstructionPermissionDeniedError,
    ):
        await service.grant_access(
            actor=actor,
            access_context=access_context,
            target_user_id=target_user_id,
            role="OWNER",
            scope_type="tenant",
            scope_id=tenant_id,
        )
