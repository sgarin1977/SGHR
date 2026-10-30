from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4


async def test_con_c001_identifier_create_audits_before_commit():
    from services.platform_company_identifiers import (
        TenantCompanyIdentifierService,
    )

    tenant_id, user_id = uuid4(), uuid4()
    profile = NS(id=uuid4(), tenant_id=tenant_id)
    identifier = NS(id=uuid4(), tenant_company_profile_id=profile.id)
    actor = NS(
        tenant_id=tenant_id, user_id=user_id, status="active",
        permissions=("platform.organization.profile.manage",),
    )
    calls = []

    def mark(name, value=None):
        calls.append(name)
        return value

    session = NS(
        commit=AsyncMock(side_effect=lambda: mark("commit")),
        rollback=AsyncMock(),
    )
    profiles = NS(get_for_tenant_for_update=AsyncMock(
        side_effect=lambda **kw: mark("profile", profile)
    ))
    identifiers = NS(create_identifier=AsyncMock(
        side_effect=lambda **kw: mark("create", identifier)
    ))
    events = NS(create_event=AsyncMock(
        side_effect=lambda **kw: mark("event")
    ))
    service = TenantCompanyIdentifierService(
        session=session, profile_repository=profiles,
        identifier_repository=identifiers, event_repository=events,
    )
    result = await service.create_identifier(
        actor=actor, country_code=" pt ", identifier_type=" VAT ",
        identifier_value=" PT 123 ", is_primary=True,
        trace_id="request-1",
    )

    assert result is identifier
    assert calls == ["profile", "create", "event", "commit"]
    session.rollback.assert_not_awaited()
    created = identifiers.create_identifier.await_args.kwargs
    assert (created["country_code"], created["identifier_type"]) == (
        "PT", "vat"
    )
    assert created["identifier_value_normalized"] == "PT123"
    event = events.create_event.await_args.kwargs
    assert (event["tenant_id"], event["user_id"]) == (
        tenant_id, user_id
    )
    assert event["entity_id"] == identifier.id


async def test_con_c001_identifier_list_allows_active_member():
    from services.platform_company_identifiers import (
        TenantCompanyIdentifierService,
    )

    tenant_id = uuid4()
    actor = NS(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=(),
    )
    profile = NS(id=uuid4(), tenant_id=tenant_id)
    identifiers = [NS(id=uuid4(), tenant_company_profile_id=profile.id)]
    profiles_repo = NS(
        get_for_tenant=AsyncMock(return_value=profile),
    )
    identifiers_repo = NS(
        list_for_tenant=AsyncMock(return_value=identifiers),
    )
    service = TenantCompanyIdentifierService(
        session=NS(),
        profile_repository=profiles_repo,
        identifier_repository=identifiers_repo,
        event_repository=NS(),
    )

    result = await service.list_identifiers(actor=actor)

    assert result == identifiers
    profiles_repo.get_for_tenant.assert_awaited_once_with(
        tenant_id=tenant_id
    )
    identifiers_repo.list_for_tenant.assert_awaited_once_with(
        tenant_id=tenant_id
    )


async def test_con_c001_identifier_list_is_empty_without_profile():
    from services.platform_company_identifiers import (
        TenantCompanyIdentifierService,
    )

    actor = NS(
        user_id=uuid4(),
        tenant_id=uuid4(),
        status="active",
        permissions=(),
    )
    identifiers_repo = NS(list_for_tenant=AsyncMock())
    service = TenantCompanyIdentifierService(
        session=NS(),
        profile_repository=NS(
            get_for_tenant=AsyncMock(return_value=None)
        ),
        identifier_repository=identifiers_repo,
        event_repository=NS(),
    )

    assert await service.list_identifiers(actor=actor) == []
    identifiers_repo.list_for_tenant.assert_not_awaited()


import pytest


@pytest.mark.parametrize(
    "constraint_name",
    (
        "uq_tenant_company_identifiers_normalized",
        "uq_tenant_company_identifiers_primary_scope",
    ),
)
async def test_identifier_create_maps_confirmed_unique_indexes_to_conflict(
    constraint_name,
):
    from sqlalchemy.exc import IntegrityError
    from services.platform_company_identifiers import (
        CompanyIdentifierConflictError,
        TenantCompanyIdentifierService,
    )

    class FakeUniqueViolation(Exception):
        sqlstate = "23505"

        def __init__(self, name):
            self.constraint_name = name
            super().__init__("private database details")

    tenant_id = uuid4()
    actor = NS(
        user_id=uuid4(),
        tenant_id=tenant_id,
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    profile = NS(id=uuid4(), tenant_id=tenant_id)
    unique_error = IntegrityError(
        "INSERT",
        {},
        FakeUniqueViolation(constraint_name),
    )
    session = NS(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    identifiers = NS(
        create_identifier=AsyncMock(side_effect=unique_error),
    )
    events = NS(create_event=AsyncMock())
    service = TenantCompanyIdentifierService(
        session=session,
        profile_repository=NS(
            get_for_tenant_for_update=AsyncMock(
                return_value=profile
            )
        ),
        identifier_repository=identifiers,
        event_repository=events,
    )

    with pytest.raises(CompanyIdentifierConflictError) as error:
        await service.create_identifier(
            actor=actor,
            country_code="PT",
            identifier_type="vat",
            identifier_value="PT 123",
        )

    assert error.value.status_code == 409
    assert error.value.code == "company_identifier_conflict"
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    events.create_event.assert_not_awaited()


@pytest.mark.parametrize("operation", ("create", "replace", "delete"))
async def test_identifier_operations_require_existing_company_profile(
    operation,
):
    from services.platform_company_identifiers import (
        CompanyIdentifierProfileNotFoundError,
        TenantCompanyIdentifierService,
    )

    actor = NS(
        tenant_id=uuid4(),
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    session = NS(commit=AsyncMock(), rollback=AsyncMock())
    profiles = NS(
        get_for_tenant_for_update=AsyncMock(return_value=None)
    )
    identifiers = NS(
        create_identifier=AsyncMock(),
        get_for_tenant_for_update=AsyncMock(),
        delete_locked_identifier=AsyncMock(),
    )
    events = NS(create_event=AsyncMock())
    service = TenantCompanyIdentifierService(
        session=session,
        profile_repository=profiles,
        identifier_repository=identifiers,
        event_repository=events,
    )

    with pytest.raises(CompanyIdentifierProfileNotFoundError) as error:
        if operation == "create":
            await service.create_identifier(
                actor=actor,
                country_code="PT",
                identifier_type="vat",
                identifier_value="PT NEW",
            )
        elif operation == "replace":
            await service.replace_identifier(
                actor=actor,
                identifier_id=uuid4(),
                country_code="PT",
                identifier_type="vat",
                identifier_value="PT NEW",
                is_primary=False,
            )
        else:
            await service.delete_identifier(
                actor=actor,
                identifier_id=uuid4(),
            )

    assert error.value.code == "company_profile_not_found"
    profiles.get_for_tenant_for_update.assert_awaited_once_with(
        tenant_id=actor.tenant_id
    )
    identifiers.create_identifier.assert_not_awaited()
    identifiers.get_for_tenant_for_update.assert_not_awaited()
    identifiers.delete_locked_identifier.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()