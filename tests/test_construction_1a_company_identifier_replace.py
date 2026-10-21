from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

from services.platform_company_identifiers import (
    TenantCompanyIdentifierService,
)


async def test_identifier_replacement_is_atomic_and_audited():
    tenant_id, user_id = uuid4(), uuid4()
    profile = NS(id=uuid4(), tenant_id=tenant_id)
    old = NS(
        id=uuid4(),
        tenant_company_profile_id=profile.id,
        country_code="PT",
        identifier_type="vat",
        identifier_value="PT OLD",
        identifier_value_normalized="PTOLD",
        is_primary=True,
    )
    new = NS(id=uuid4(), tenant_company_profile_id=profile.id)
    actor = NS(
        tenant_id=tenant_id, user_id=user_id, status="active",
        permissions=("platform.organization.profile.manage",),
    )
    calls = []

    def step(name, value=None):
        calls.append(name)
        return value

    session = NS(
        commit=AsyncMock(side_effect=lambda: step("commit")),
        rollback=AsyncMock(),
    )
    profiles = NS(get_for_tenant_for_update=AsyncMock(
        side_effect=lambda **kw: step("profile", profile)
    ))
    identifiers = NS(
        get_for_tenant_for_update=AsyncMock(
            side_effect=lambda **kw: step("lock", old)
        ),
        delete_locked_identifier=AsyncMock(
            side_effect=lambda **kw: step("delete")
        ),
        create_identifier=AsyncMock(
            side_effect=lambda **kw: step("create", new)
        ),
    )
    events = NS(create_event=AsyncMock(
        side_effect=lambda **kw: step("event")
    ))
    service = TenantCompanyIdentifierService(
        session=session, profile_repository=profiles,
        identifier_repository=identifiers, event_repository=events,
    )

    result = await service.replace_identifier(
        actor=actor,
        identifier_id=old.id,
        country_code="PT",
        identifier_type="vat",
        identifier_value="PT NEW",
        is_primary=True,
        trace_id="request-2",
    )

    assert result is new
    assert calls == [
        "profile", "lock", "delete", "create", "event", "commit"
    ]
    session.rollback.assert_not_awaited()
    created = identifiers.create_identifier.await_args.kwargs
    assert created["identifier_value_normalized"] == "PTNEW"
    payload = events.create_event.await_args.kwargs["payload"]
    assert {
        "field": "identifier_value",
        "old_value": "PT OLD",
        "new_value": "PT NEW",
    } in payload["changes"]
