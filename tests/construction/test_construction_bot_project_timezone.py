"""Local timezone selection, API boundary and original-preserving display."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from typing import Literal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import Query
from pydantic import SecretStr

from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_display_timezone import (
    ConstructionDisplayTimezoneReader,
)
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_project_presentation import ProjectPresentationResult
from ui.construction_project_card import timestamp, project_text, tr, LANGUAGES
from utils.construction_display_timezone import validate_display_timezone


def actor(zone=None):
    return ApiActorContext(
        user_id=uuid4(), tenant_id=uuid4(), active_role="client",
        roles=("client",), language_code="uk", timezone=zone, status="active",
    )


def setup_reader(current, organization_zone):
    session = NS(
        new=(), dirty=(), deleted=(), no_autoflush=nullcontext(),
        expire_all=Mock(), commit=AsyncMock(), flush=AsyncMock(),
    )
    metadata = {"unrelated_private_field": "must-not-be-returned"}
    if organization_zone is not None:
        metadata["regional_settings"] = {
            "country_code": "UA", "timezone": organization_zone,
        }
    row = {
        "tenant_id": current.tenant_id, "metadata": metadata,
        "updated_at": datetime(2026, 11, 12, tzinfo=timezone.utc),
        "row_version": 3,
    }
    identity = NS(
        session=session, require_actor=AsyncMock(return_value=current),
    )
    repository = NS(session=session, get_active=AsyncMock(return_value=row))
    reader = ConstructionDisplayTimezoneReader(
        session=session, identity=identity, repository=repository,
    )
    return reader, session, identity, repository, row


@pytest.mark.asyncio
@pytest.mark.parametrize("user_zone,org_zone,expected,source", [
    ("America/New_York", "Europe/Kyiv", "America/New_York", "USER"),
    ("UTC", "Europe/Kyiv", "UTC", "USER"),
    (None, "Europe/Kyiv", "Europe/Kyiv", "ORGANIZATION"),
    (None, None, None, "UNCONFIGURED"),
])
async def test_fresh_identity_overrides_organization_without_settings_permission(
    user_zone, org_zone, expected, source,
):
    current = actor(user_zone)
    reader, session, identity, repository, row = setup_reader(current, org_zone)
    before = deepcopy(row)
    result = await reader.read_for_actor(actor=current)
    assert result == {"timezone": expected, "source": source}
    assert row == before
    assert "must-not-be-returned" not in repr(result)
    assert current.permissions == ()
    assert identity.require_actor.await_count == 2
    repository.get_active.assert_awaited_once_with(tenant_id=current.tenant_id)
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_latest_profile_setting_is_used_after_wait():
    current = actor("Europe/Kyiv")
    reader, _, identity, _, _ = setup_reader(current, "Europe/Lisbon")
    identity.require_actor.side_effect = [
        current, replace(current, timezone="America/New_York"),
    ]
    assert await reader.read_for_actor(actor=current) == {
        "timezone": "America/New_York", "source": "USER",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "identity", "foreign_tenant"])
async def test_changed_identity_or_membership_cannot_supply_context(failure):
    current = actor()
    reader, session, identity, _, row = setup_reader(current, "Europe/Kyiv")
    if failure == "membership":
        identity.require_actor.side_effect = [
            current, ApiIdentityAccessError("membership removed"),
        ]
    elif failure == "identity":
        identity.require_actor.side_effect = [
            current, replace(current, user_id=uuid4()),
        ]
    else:
        row["tenant_id"] = uuid4()
    with pytest.raises(Exception):
        await reader.read_for_actor(actor=current)
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("value", [
    {"timezone": None, "source": "USER"},
    {"timezone": "Europe/Kyiv", "source": "UNCONFIGURED"},
    {"timezone": "localtime", "source": "USER"},
    {"timezone": "right/Europe/Kyiv", "source": "USER"},
    {"timezone": "Missing/Timezone", "source": "ORGANIZATION"},
    {"timezone": " Europe/Kyiv", "source": "USER"},
    {"timezone": True, "source": "USER"},
    {"timezone": "UTC", "source": "ADMIN"},
    {"timezone": "UTC", "source": "USER", "tenant_id": "caller"},
])
def test_invalid_context_is_not_silently_replaced(value):
    with pytest.raises(ValueError):
        validate_display_timezone(value)


@pytest.mark.parametrize("instant,expected", [
    ("2026-03-29T00:30:00Z", "29.03.2026 02:30 Europe/Kyiv (UTC+02:00)"),
    ("2026-03-29T01:30:00Z", "29.03.2026 04:30 Europe/Kyiv (UTC+03:00)"),
    ("2026-10-25T00:30:00Z", "25.10.2026 03:30 Europe/Kyiv (UTC+03:00)"),
    ("2026-10-25T01:30:00Z", "25.10.2026 03:30 Europe/Kyiv (UTC+02:00)"),
])
def test_dst_and_repeated_local_hour_are_unambiguous(instant, expected):
    assert timestamp(instant, {
        "timezone": "Europe/Kyiv", "source": "ORGANIZATION",
    }) == expected


@pytest.mark.parametrize("value", ["2026-11-12T10:00:00", "", None, 123])
def test_naive_or_invalid_instants_are_rejected(value):
    with pytest.raises(ValueError):
        timestamp(value)


@pytest.mark.parametrize("language", LANGUAGES)
def test_card_preserves_utc_original_and_marks_unconfigured_timezone(language):
    result = {
        "actor_user_id": str(uuid4()), "organization": "Organization",
        "display_timezone": {"timezone": None, "source": "UNCONFIGURED"},
        "item": {
            "name": "Project", "status": "draft", "currency": "EUR",
            "responsible_user_id": None, "address_raw": "Original address",
            "address_formatted": None, "address_verification_status": "pending",
            "comment": None, "created_at": "2026-11-12T10:00:00Z",
            "updated_at": "2026-11-12T10:01:00Z",
        },
    }
    before = deepcopy(result)
    assert tr("timezone_unconfigured", language) in project_text(result, language)
    result["display_timezone"] = {
        "timezone": "Europe/Kyiv", "source": "ORGANIZATION",
    }
    text = project_text(result, language)
    assert "12.11.2026 12:00 Europe/Kyiv (UTC+02:00)" in text
    assert tr("timezone_unconfigured", language) not in text
    assert result["item"] == before["item"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", [
    "project_catalog_card", "project_card", "read_created_project",
])
async def test_three_card_entries_forward_authorized_context(method):
    user, tenant, project = uuid4(), uuid4(), uuid4()
    zone = {"timezone": "Europe/Kyiv", "source": "ORGANIZATION"}
    item = {"id": str(project), "created_at": "2026-11-12T10:00:00Z"}
    read = AsyncMock(return_value=ProjectPresentationResult(
        item, {"capabilities": {}}, "local",
    ))
    entry = ConstructionBotApiEntry(
        api=NS(construction=NS(project_details=read, project_card=read)),
        context=None, sessions=None,
    )
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(user, tenant, SecretStr("local-only")),
        {"name": "Organization", "language": "uk", "display_timezone": zone},
    ))
    arguments = {
        "telegram_user_id": 123, "tenant_id": tenant,
        "project_id": project, "expected_user_id": user,
    }
    if method == "project_card":
        arguments["client_id"] = uuid4()
    result = await getattr(entry, method)(**arguments)
    assert entry._presentation.await_args.kwargs["include_timezone"] is True
    assert result["display_timezone"] == zone
    assert result["item"] is item


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [False, True])
async def test_transport_uses_existing_navigation_and_validates_context(bad):
    tenant, user = uuid4(), uuid4()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": {
                "tenant_id": str(tenant), "name": "Organization",
                "currency": "EUR", "roles": ["OBSERVER"],
                "can_read_leads": False,
            },
            "meta": {"display_timezone": {
                "timezone": "Missing/Timezone" if bad else "Europe/Kyiv",
                "source": "ORGANIZATION",
            }},
            "request_id": "local",
        })

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(respond),
    ) as http:
        client = ConstructionApiClient(http=http)
        session = PlatformApiClientSession(user, tenant, SecretStr("local-only"))
        if bad:
            with pytest.raises(ConstructionApiClientError) as caught:
                await client.navigation(session=session, include_timezone=True)
            assert caught.value.status_code == 502
        else:
            result = await client.navigation(session=session, include_timezone=True)
            assert result.meta["display_timezone"]["timezone"] == "Europe/Kyiv"
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.path == "/api/v1/construction/ui/navigation-context"
    assert list(calls[0].url.params.multi_items()) == [("include_timezone", "true")]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["plain", "timezone", "denied", "invalid", "duplicate"])
async def test_registered_navigation_keeps_authorization_before_timezone(monkeypatch, case):
    from construction_rate_limit_fixture import with_local_construction_rate_limit
    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_tenant_module_access_service, get_tenant_suite_access_service,
    )
    from api.routes.construction import (
        get_construction_access_context, get_construction_scoped_session,
    )
    import api.routes.construction_navigation as route
    from api.construction_timezone_presentation import get_optional_navigation_timezone
    from services.construction.construction_permissions import (
        ConstructionPermissionDeniedError,
    )

    current = actor()
    base = AsyncMock(return_value={
        "tenant_id": str(current.tenant_id), "name": "Organization",
        "currency": "EUR", "roles": ["OBSERVER"], "can_read_leads": False,
    })
    reader = NS(read_for_actor=AsyncMock(return_value={
        "timezone": "Europe/Kyiv", "source": "ORGANIZATION",
    }))
    if case == "denied":
        base.side_effect = ConstructionPermissionDeniedError()
    monkeypatch.setattr(route, "read_construction_navigation", base)

    app = with_local_construction_rate_limit(create_app())
    app.dependency_overrides[get_current_actor] = lambda: current
    app.dependency_overrides[get_construction_access_context] = lambda: object()
    app.dependency_overrides[get_construction_scoped_session] = lambda: object()
    app.dependency_overrides[route.get_navigation_access] = lambda: object()
    app.dependency_overrides[route.get_navigation_organization] = lambda: object()
    gates = NS(require_enabled=AsyncMock())
    app.dependency_overrides[get_tenant_suite_access_service] = lambda: gates
    app.dependency_overrides[get_tenant_module_access_service] = lambda: gates

    def optional(
        include_timezone: Literal["false", "true"] = Query(default="false"),
    ):
        return reader if include_timezone == "true" else None

    app.dependency_overrides[get_optional_navigation_timezone] = optional
    params = [] if case == "plain" else [("include_timezone", "true")]
    if case == "invalid":
        params.append(("tenant_id", str(uuid4())))
    elif case == "duplicate":
        params.append(("include_timezone", "true"))
    async with httpx.AsyncClient(
        base_url="http://testserver",
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
    ) as http:
        response = await http.get(
            "/api/v1/construction/ui/navigation-context", params=params,
        )

    expected = 403 if case == "denied" else 422 if case in {
        "invalid", "duplicate",
    } else 200
    assert response.status_code == expected, response.text
    if case == "timezone":
        assert response.json()["meta"] == {"display_timezone": {
            "timezone": "Europe/Kyiv", "source": "ORGANIZATION",
        }}
        reader.read_for_actor.assert_awaited_once_with(actor=current)
    else:
        reader.read_for_actor.assert_not_awaited()
    if case == "plain":
        assert response.json()["meta"] == {}
