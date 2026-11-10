from services.construction.construction_permissions import CONSTRUCTION_ROLE_PERMISSIONS
from types import SimpleNamespace


def test_con_c002_contact_overrides_inherit_platform_profile():
    from services.construction.construction_tenant_settings import (
        resolve_construction_contact_fields,
    )

    profile = SimpleNamespace(
        phone="+351912345678",
        email="office@example.pt",
        website="https://example.pt",
    )
    settings = SimpleNamespace(
        phone_override="   ",
        email_override="site@example.pt",
        website_override=None,
    )

    result = resolve_construction_contact_fields(
        profile=profile,
        settings=settings,
    )

    assert result == {
        "phone": "+351912345678",
        "email": "site@example.pt",
        "website": "https://example.pt",
    }
    assert settings.phone_override == "   "


def test_con_c002_missing_profile_does_not_create_contact_values():
    from services.construction.construction_tenant_settings import (
        resolve_construction_contact_fields,
    )

    assert resolve_construction_contact_fields(
        profile=None,
        settings=None,
    ) == {
        "phone": None,
        "email": None,
        "website": None,
    }

    settings = SimpleNamespace(
        phone_override=None,
        email_override="site@example.pt",
        website_override=None,
    )
    assert resolve_construction_contact_fields(
        profile=None,
        settings=settings,
    ) == {
        "phone": None,
        "email": "site@example.pt",
        "website": None,
    }


def test_con_c002_tenant_settings_model_has_only_confirmed_overrides():
    from sqlalchemy import BigInteger, DateTime, String

    from database.models import ConstructionTenantSetting

    table = ConstructionTenantSetting.__table__
    assert table.name == "construction_tenant_settings"
    assert set(table.columns.keys()) == {
        "tenant_id",
        "display_name_override",
        "phone_override",
        "email_override",
        "website_override",
        "logo_file_id_override",
        "document_header_text",
        "document_footer_text",
        "accent_color",
        "created_at",
        "updated_at",
        "row_version",
        "onboarding_current_step",
        "onboarding_step_states",
        "onboarding_tax_profile_id",
    }

    assert table.c.tenant_id.primary_key
    assert table.c.tenant_id.nullable is False
    assert isinstance(table.c.row_version.type, BigInteger)
    assert isinstance(table.c.created_at.type, DateTime)
    assert isinstance(table.c.updated_at.type, DateTime)
    assert table.c.accent_color.type.length == 7
    assert isinstance(table.c.display_name_override.type, String)
    assert table.c.display_name_override.type.length == 255
    assert table.c.phone_override.type.length == 50
    assert table.c.email_override.type.length == 320
    assert table.c.website_override.type.length == 2048
    assert (
        ConstructionTenantSetting.__mapper__.version_id_col
        is table.c.row_version
    )

    foreign_keys = {
        (column.name, key.target_fullname)
        for column in table.columns
        for key in column.foreign_keys
    }
    assert ("tenant_id", "tenants.id") in foreign_keys
    assert (
        "logo_file_id_override",
        "file_storage_objects.id",
    ) in foreign_keys



async def test_con_c002_repository_reads_only_requested_tenant():
    from uuid import uuid4

    from database.repositories.construction.construction_tenant_settings import (
        ConstructionTenantSettingsRepository,
    )

    tenant_id = uuid4()
    calls = []

    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        async def execute(self, statement):
            calls.append(statement)
            return Result()

    repository = ConstructionTenantSettingsRepository(Session())
    assert await repository.get_for_tenant(tenant_id=tenant_id) is None

    assert len(calls) == 1
    compiled = calls[0].compile()
    assert tenant_id in compiled.params.values()
    sql = str(compiled)
    assert "construction_tenant_settings.tenant_id" in sql
    assert "WHERE" in sql


async def test_con_c002_service_reads_only_actor_tenant():
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsScopeError,
        ConstructionTenantSettingsService,
    )

    tenant_id = uuid4()
    requested = []

    class Reader:
        def __init__(self, result):
            self.result = result

        async def get_for_tenant(self, *, tenant_id):
            requested.append(tenant_id)
            return self.result

    actor = SimpleNamespace(tenant_id=tenant_id, status="active")
    settings = SimpleNamespace(tenant_id=tenant_id)

    service = ConstructionTenantSettingsService(
        settings_reader=Reader(settings)
    )
    assert await service.get_for_actor(actor=actor) is settings
    assert requested == [tenant_id]

    service = ConstructionTenantSettingsService(
        settings_reader=Reader(None)
    )
    assert await service.get_for_actor(actor=actor) is None

    service = ConstructionTenantSettingsService(
        settings_reader=Reader(
            SimpleNamespace(tenant_id=uuid4())
        )
    )
    with pytest.raises(ConstructionTenantSettingsScopeError):
        await service.get_for_actor(actor=actor)

    requested.clear()
    inactive = SimpleNamespace(tenant_id=tenant_id, status="blocked")
    with pytest.raises(ConstructionTenantSettingsScopeError):
        await service.get_for_actor(actor=inactive)
    assert requested == []


def test_con_c002_logo_reference_inherits_platform_profile():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction.construction_tenant_settings import (
        resolve_construction_logo_file_id,
    )

    platform_logo_id = uuid4()
    override_logo_id = uuid4()
    profile = SimpleNamespace(logo_file_id=platform_logo_id)

    assert resolve_construction_logo_file_id(
        profile=profile,
        settings=None,
    ) == platform_logo_id

    assert resolve_construction_logo_file_id(
        profile=profile,
        settings=SimpleNamespace(logo_file_id_override=None),
    ) == platform_logo_id

    assert resolve_construction_logo_file_id(
        profile=profile,
        settings=SimpleNamespace(
            logo_file_id_override=override_logo_id
        ),
    ) == override_logo_id

    assert resolve_construction_logo_file_id(
        profile=None,
        settings=None,
    ) is None


def test_con_c002_document_text_overrides_normalize_blank():
    from types import SimpleNamespace

    from services.construction.construction_tenant_settings import (
        resolve_construction_document_texts,
    )

    assert resolve_construction_document_texts(
        settings=None
    ) == {
        "document_header_text": None,
        "document_footer_text": None,
    }

    settings = SimpleNamespace(
        document_header_text="  Quote No. 1\n",
        document_footer_text="  \t  ",
    )
    assert resolve_construction_document_texts(
        settings=settings
    ) == {
        "document_header_text": "  Quote No. 1\n",
        "document_footer_text": None,
    }


def test_con_c002_accent_color_inherits_platform_ui():
    from types import SimpleNamespace

    import pytest

    from services.construction.construction_tenant_settings import (
        resolve_construction_accent_color,
    )

    fallback = "#123ABC"
    assert resolve_construction_accent_color(
        settings=None,
        platform_ui_accent_color=fallback,
    ) == fallback
    assert resolve_construction_accent_color(
        settings=SimpleNamespace(accent_color="  "),
        platform_ui_accent_color=fallback,
    ) == fallback
    assert resolve_construction_accent_color(
        settings=SimpleNamespace(accent_color="#a1B2c3"),
        platform_ui_accent_color=fallback,
    ) == "#a1B2c3"
    with pytest.raises(ValueError):
        resolve_construction_accent_color(
            settings=SimpleNamespace(accent_color="#123"),
            platform_ui_accent_color=fallback,
        )


def test_con_c002_normalizes_confirmed_text_overrides():
    import pytest

    from services.construction.construction_tenant_settings import (
        normalize_construction_text_overrides,
    )

    assert normalize_construction_text_overrides(
        display_name_override="   ",
        phone_override=None,
        document_header_text="  Quote  ",
        accent_color="#a1B2c3",
    ) == {
        "display_name_override": None,
        "phone_override": None,
        "document_header_text": "  Quote  ",
        "accent_color": "#a1B2c3",
    }

    with pytest.raises(ValueError):
        normalize_construction_text_overrides(
            display_name_override="x" * 256
        )
    with pytest.raises(ValueError):
        normalize_construction_text_overrides(
            accent_color="#123"
        )
    with pytest.raises(ValueError):
        normalize_construction_text_overrides(
            legal_name_override="Not allowed"
        )


def test_con_c002_display_name_uses_confirmed_precedence():
    from types import SimpleNamespace

    from services.construction.construction_tenant_settings import (
        resolve_construction_display_name,
    )

    def resolve(override, platform_name, tenant_name):
        return resolve_construction_display_name(
            settings=SimpleNamespace(
                display_name_override=override
            ),
            platform_display_name=platform_name,
            tenant_ui_name=tenant_name,
        )

    assert resolve(
        "Construction Brand",
        "Platform Company",
        "Tenant UI",
    ) == "Construction Brand"
    assert resolve(
        "   ",
        "Platform Company",
        "Tenant UI",
    ) == "Platform Company"
    assert resolve(
        None,
        None,
        "Tenant UI",
    ) == "Tenant UI"
    assert resolve(None, None, None) is None


def test_con_c002_settings_manage_requires_active_tenant_grant():
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        CONSTRUCTION_SETTINGS_MANAGE_PERMISSION,
        ConstructionTenantSettingsPermissionDeniedError,
        require_construction_settings_manage,
    )

    assert CONSTRUCTION_SETTINGS_MANAGE_PERMISSION == (
        "construction.settings.manage"
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        user_id=user_id, tenant_id=tenant_id, status="active"
    )

    def context(role, scope_type="tenant", scope_id=None):
        return ConstructionAccessContext(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=(role,),
            permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get(role, frozenset()))),
            grants=(ConstructionGrantContext(
                role=role,
                scope_type=scope_type,
                scope_id=scope_id or tenant_id,
            ),),
        )

    for role in ("OWNER", "ADMIN"):
        require_construction_settings_manage(
            actor=actor, access_context=context(role)
        )

    denied = (
        context("SURVEYOR"),
        context("OWNER", "project", uuid4()),
        context("ADMIN", "tenant", uuid4()),
        ConstructionAccessContext(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=("OWNER",),
            permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
            grants=(),
        ),
    )
    for access_context in denied:
        with pytest.raises(
            ConstructionTenantSettingsPermissionDeniedError
        ):
            require_construction_settings_manage(
                actor=actor, access_context=access_context
            )


async def test_con_c002_repository_creates_confirmed_overrides_without_commit():
    from uuid import uuid4

    import pytest

    from database.repositories.construction.construction_tenant_settings import (
        ConstructionTenantSettingsRepository,
    )

    added = []
    flushed = []

    class Session:
        def add(self, record):
            added.append(record)

        async def flush(self):
            flushed.append(True)

        async def commit(self):
            raise AssertionError("Repository must not commit")

    repository = ConstructionTenantSettingsRepository(Session())
    tenant_id = uuid4()
    settings = await repository.create_settings(
        tenant_id=tenant_id,
        values={"display_name_override": "Site Team"},
    )

    assert settings.tenant_id == tenant_id
    assert settings.display_name_override == "Site Team"
    assert added == [settings]
    assert flushed == [True]

    with pytest.raises(ValueError):
        await repository.create_settings(
            tenant_id=tenant_id,
            values={"legal_name": "Not a Construction override"},
        )
    assert added == [settings]


async def test_con_c002_repository_locks_refreshed_tenant_settings():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.construction.construction_tenant_settings import (
        ConstructionTenantSettingsRepository,
    )

    tenant_id = uuid4()
    record = object()
    statements = []

    class Result:
        def scalar_one_or_none(self):
            return record

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return Result()

    repository = ConstructionTenantSettingsRepository(Session())
    result = await repository.get_for_tenant_for_update(
        tenant_id=tenant_id
    )

    assert result is record
    assert len(statements) == 1
    statement = statements[0]
    assert tenant_id in statement.compile().params.values()
    assert statement.get_execution_options()["populate_existing"] is True
    assert "FOR UPDATE" in str(
        statement.compile(dialect=postgresql.dialect())
    )


async def test_con_c002_repository_updates_only_overrides_without_commit():
    from types import SimpleNamespace
    from uuid import uuid4

    import pytest

    from database.repositories.construction.construction_tenant_settings import (
        ConstructionTenantSettingsRepository,
    )

    events = []

    class Session:
        async def flush(self):
            events.append("flush")

        async def commit(self):
            raise AssertionError("Repository must not commit")

    repository = ConstructionTenantSettingsRepository(Session())
    tenant_id = uuid4()
    settings = SimpleNamespace(
        tenant_id=tenant_id,
        phone_override=None,
    )

    result = await repository.update_settings(
        settings=settings,
        values={"phone_override": "+351912345678"},
    )
    assert result is settings
    assert settings.tenant_id == tenant_id
    assert settings.phone_override == "+351912345678"
    assert events == ["flush"]

    for forbidden in ({}, {"tenant_id": uuid4()}, {"legal_name": "X"}):
        with pytest.raises(ValueError):
            await repository.update_settings(
                settings=settings,
                values=forbidden,
            )
    assert events == ["flush"]


async def test_con_c002_create_settings_audits_before_commit():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsService,
    )

    calls = []
    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id
        ),),
    )

    class Session:
        async def commit(self):
            calls.append(("commit", None))

        async def rollback(self):
            calls.append(("rollback", None))

    class Repository:
        async def get_for_tenant(self, *, tenant_id):
            calls.append(("lookup", tenant_id))
            return None

        async def create_settings(self, *, tenant_id, values):
            calls.append(("create", tenant_id))
            return SimpleNamespace(tenant_id=tenant_id, **values)

    class Events:
        async def create_event(self, **values):
            calls.append(("audit", values))

    service = ConstructionTenantSettingsService(
        settings_reader=Repository(),
        session=Session(),
        event_repository=Events(),
    )
    settings = await service.create_for_actor(
        actor=actor,
        access_context=access_context,
        values={"display_name_override": "Site Team"},
        trace_id="trace-c002",
    )

    assert settings.tenant_id == tenant_id
    assert [item[0] for item in calls] == [
        "lookup", "create", "audit", "commit"
    ]
    audit = calls[2][1]
    assert audit["event_type"] == "construction_tenant_settings_created"
    assert audit["tenant_id"] == tenant_id
    assert audit["user_id"] == user_id
    assert audit["trace_id"] == "trace-c002"


async def test_con_c002_audit_failure_rolls_back_settings_creation():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsOperationError,
        ConstructionTenantSettingsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id
        ),),
    )
    repository = SimpleNamespace(
        get_for_tenant=AsyncMock(return_value=None),
        create_settings=AsyncMock(
            return_value=SimpleNamespace(tenant_id=tenant_id)
        ),
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    events = SimpleNamespace(
        create_event=AsyncMock(
            side_effect=RuntimeError("private provider token")
        ),
    )
    service = ConstructionTenantSettingsService(
        settings_reader=repository,
        session=session,
        event_repository=events,
    )

    with pytest.raises(
        ConstructionTenantSettingsOperationError
    ) as error:
        await service.create_for_actor(
            actor=actor,
            access_context=access_context,
            values={"display_name_override": "Site Team"},
        )

    assert "private provider token" not in str(error.value)
    repository.create_settings.assert_awaited_once()
    events.create_event.assert_awaited_once()
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


async def test_con_c002_concurrent_create_maps_only_settings_pk_to_conflict():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest
    from sqlalchemy.exc import IntegrityError

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsConflictError,
        ConstructionTenantSettingsOperationError,
        ConstructionTenantSettingsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id
        ),),
    )

    class DatabaseError(Exception):
        def __init__(self, sqlstate, constraint_name):
            self.sqlstate = sqlstate
            self.constraint_name = constraint_name
            super().__init__("private database detail")

    cases = (
        ("23505", "construction_tenant_settings_pkey",
         ConstructionTenantSettingsConflictError),
        ("23505", "some_other_constraint",
         ConstructionTenantSettingsOperationError),
        ("23503", "construction_tenant_settings_pkey",
         ConstructionTenantSettingsOperationError),
    )
    for sqlstate, constraint, expected in cases:
        failure = IntegrityError(
            "INSERT", {},
            DatabaseError(sqlstate, constraint),
        )
        repository = SimpleNamespace(
            get_for_tenant=AsyncMock(return_value=None),
            create_settings=AsyncMock(side_effect=failure),
        )
        session = SimpleNamespace(
            commit=AsyncMock(), rollback=AsyncMock()
        )
        events = SimpleNamespace(create_event=AsyncMock())
        service = ConstructionTenantSettingsService(
            settings_reader=repository,
            session=session,
            event_repository=events,
        )

        with pytest.raises(expected):
            await service.create_for_actor(
                actor=actor,
                access_context=access_context,
                values={"display_name_override": "Site Team"},
            )

        session.rollback.assert_awaited_once_with()
        session.commit.assert_not_awaited()
        events.create_event.assert_not_awaited()


async def test_con_c002_update_rejects_stale_row_version_before_mutation():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsRowVersionConflictError,
        ConstructionTenantSettingsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("ADMIN", frozenset()))),
        grants=(ConstructionGrantContext(
            role="ADMIN", scope_type="tenant", scope_id=tenant_id
        ),),
    )
    repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(
            return_value=SimpleNamespace(
                tenant_id=tenant_id, row_version=3
            )
        ),
        update_settings=AsyncMock(),
    )
    session = SimpleNamespace(
        commit=AsyncMock(), rollback=AsyncMock()
    )
    events = SimpleNamespace(create_event=AsyncMock())
    service = ConstructionTenantSettingsService(
        settings_reader=repository,
        session=session,
        event_repository=events,
    )

    with pytest.raises(
        ConstructionTenantSettingsRowVersionConflictError
    ):
        await service.update_for_actor(
            actor=actor,
            access_context=access_context,
            expected_row_version=2,
            changes={"phone_override": "+351912345678"},
        )

    repository.get_for_tenant_for_update.assert_awaited_once_with(
        tenant_id=tenant_id
    )
    repository.update_settings.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()


async def test_con_c002_update_audits_changes_before_commit():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("ADMIN", frozenset()))),
        grants=(ConstructionGrantContext(
            role="ADMIN", scope_type="tenant", scope_id=tenant_id
        ),),
    )
    settings = SimpleNamespace(
        tenant_id=tenant_id,
        row_version=4,
        display_name_override="Old name",
    )
    calls = []
    audit = {}

    class Repository:
        async def get_for_tenant_for_update(self, *, tenant_id):
            calls.append("lock")
            return settings

        async def update_settings(self, *, settings, values):
            calls.append("update")
            settings.display_name_override = values[
                "display_name_override"
            ]
            return settings

    class Events:
        async def create_event(self, **values):
            calls.append("audit")
            audit.update(values)

    class Session:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    service = ConstructionTenantSettingsService(
        settings_reader=Repository(),
        session=Session(),
        event_repository=Events(),
    )
    result = await service.update_for_actor(
        actor=actor,
        access_context=access_context,
        expected_row_version=4,
        changes={"display_name_override": "New name"},
        trace_id="trace-c002-update",
    )

    assert result.display_name_override == "New name"
    assert calls == ["lock", "update", "audit", "commit"]
    assert audit["event_type"] == "construction_tenant_settings_updated"
    assert audit["tenant_id"] == tenant_id
    assert audit["user_id"] == user_id
    assert audit["trace_id"] == "trace-c002-update"
    assert audit["payload"]["changes"] == [{
        "field": "display_name_override",
        "old_value": "Old name",
        "new_value": "New name",
    }]


async def test_con_c002_blank_override_noop_preserves_version_without_audit():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id
        ),),
    )
    settings = SimpleNamespace(
        tenant_id=tenant_id,
        row_version=4,
        phone_override=None,
    )
    repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=settings),
        update_settings=AsyncMock(),
    )
    session = SimpleNamespace(
        commit=AsyncMock(), rollback=AsyncMock()
    )
    events = SimpleNamespace(create_event=AsyncMock())
    service = ConstructionTenantSettingsService(
        settings_reader=repository,
        session=session,
        event_repository=events,
    )

    result = await service.update_for_actor(
        actor=actor,
        access_context=access_context,
        expected_row_version=4,
        changes={"phone_override": "   "},
    )

    assert result is settings
    assert settings.phone_override is None
    assert settings.row_version == 4
    repository.update_settings.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.commit.assert_awaited_once_with()
    session.rollback.assert_not_awaited()


async def test_con_c002_update_audit_failure_rolls_back():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsOperationError,
        ConstructionTenantSettingsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("ADMIN",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("ADMIN", frozenset()))),
        grants=(ConstructionGrantContext(
            role="ADMIN", scope_type="tenant", scope_id=tenant_id
        ),),
    )
    settings = SimpleNamespace(
        tenant_id=tenant_id,
        row_version=2,
        document_footer_text="Old",
    )
    calls = []

    async def update_settings(*, settings, values):
        calls.append("update")
        settings.document_footer_text = values["document_footer_text"]
        return settings

    async def create_event(**values):
        calls.append("audit")
        raise RuntimeError("private audit failure detail")

    async def rollback():
        calls.append("rollback")

    repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=settings),
        update_settings=update_settings,
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=rollback,
    )
    service = ConstructionTenantSettingsService(
        settings_reader=repository,
        session=session,
        event_repository=SimpleNamespace(create_event=create_event),
    )

    with pytest.raises(
        ConstructionTenantSettingsOperationError
    ) as error:
        await service.update_for_actor(
            actor=actor,
            access_context=access_context,
            expected_row_version=2,
            changes={"document_footer_text": "New"},
        )

    assert calls == ["update", "audit", "rollback"]
    assert "private audit failure detail" not in str(error.value)
    session.commit.assert_not_awaited()


async def test_con_c002_orm_stale_update_maps_to_version_conflict():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest
    from sqlalchemy.orm.exc import StaleDataError

    from services.construction.construction_permissions import (
        ConstructionAccessContext,
        ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsRowVersionConflictError,
        ConstructionTenantSettingsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    actor = SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, status="active"
    )
    access_context = ConstructionAccessContext(
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("OWNER",),
        permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant", scope_id=tenant_id
        ),),
    )
    settings = SimpleNamespace(
        tenant_id=tenant_id,
        row_version=3,
        phone_override="Old",
    )
    repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=settings),
        update_settings=AsyncMock(
            side_effect=StaleDataError("private ORM update detail")
        ),
    )
    session = SimpleNamespace(
        commit=AsyncMock(), rollback=AsyncMock()
    )
    events = SimpleNamespace(create_event=AsyncMock())
    service = ConstructionTenantSettingsService(
        settings_reader=repository,
        session=session,
        event_repository=events,
    )

    with pytest.raises(
        ConstructionTenantSettingsRowVersionConflictError
    ) as error:
        await service.update_for_actor(
            actor=actor,
            access_context=access_context,
            expected_row_version=3,
            changes={"phone_override": "New"},
        )

    assert "private ORM update detail" not in str(error.value)
    session.rollback.assert_awaited_once_with()
    session.commit.assert_not_awaited()
    events.create_event.assert_not_awaited()


async def test_c002_update_missing_settings_is_not_found():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    import pytest

    from services.construction.construction_permissions import (
        ConstructionAccessContext, ConstructionGrantContext,
    )
    from services.construction.construction_tenant_settings import (
        ConstructionTenantSettingsNotFoundError,
        ConstructionTenantSettingsService,
    )

    actor = SimpleNamespace(
        tenant_id=uuid4(), user_id=uuid4(), status="active",
    )
    context = ConstructionAccessContext(
        user_id=actor.user_id, tenant_id=actor.tenant_id,
        roles=("OWNER",), permissions=tuple(sorted(CONSTRUCTION_ROLE_PERMISSIONS.get("OWNER", frozenset()))),
        grants=(ConstructionGrantContext(
            role="OWNER", scope_type="tenant",
            scope_id=actor.tenant_id,
        ),),
    )
    repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=None),
        update_settings=AsyncMock(),
    )
    session = SimpleNamespace(
        commit=AsyncMock(), rollback=AsyncMock(),
    )
    events = SimpleNamespace(create_event=AsyncMock())
    service = ConstructionTenantSettingsService(
        settings_reader=repository, session=session,
        event_repository=events,
    )

    with pytest.raises(ConstructionTenantSettingsNotFoundError):
        await service.update_for_actor(
            actor=actor, access_context=context,
            expected_row_version=1,
            changes={"display_name_override": "Crew"},
        )

    repository.update_settings.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


def test_c002_effective_presentation_composes_confirmed_overrides():
    from types import SimpleNamespace
    from uuid import uuid4
    from services.construction.construction_tenant_settings import (
        resolve_construction_company_presentation,
    )

    logo = uuid4()
    profile = SimpleNamespace(
        phone="+351111111111", email="office@example.com",
        website="https://example.com", logo_file_id=logo,
        legal_name="Legal Company",
    )
    settings = SimpleNamespace(
        display_name_override="Construction Brand",
        phone_override=None, email_override="build@example.com",
        website_override=None, logo_file_id_override=None,
        document_header_text="Header", document_footer_text=None,
        accent_color="#123ABC",
    )

    result = resolve_construction_company_presentation(
        profile=profile, settings=settings,
        platform_display_name="Platform Brand",
        tenant_ui_name="Tenant UI", platform_ui_accent_color="#AABBCC",
    )

    assert result == {
        "display_name": "Construction Brand",
        "phone": "+351111111111",
        "email": "build@example.com",
        "website": "https://example.com",
        "logo_file_id": logo,
        "document_header_text": "Header",
        "document_footer_text": None,
        "accent_color": "#123ABC",
    }
    assert "legal_name" not in result


def test_c002_effective_presentation_uses_ui_fallback_without_profile():
    from services.construction.construction_tenant_settings import (
        resolve_construction_company_presentation,
    )

    result = resolve_construction_company_presentation(
        profile=None, settings=None, platform_display_name=None,
        tenant_ui_name="Tenant UI", platform_ui_accent_color="#AABBCC",
    )
    assert result["display_name"] == "Tenant UI"
    assert result["accent_color"] == "#AABBCC"
    assert result["phone"] is None
    assert result["logo_file_id"] is None

# onboarding_explicit_tax_profile_v1
