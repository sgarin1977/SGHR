"""Local company profile transport and private read-only bot card."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from api.app import create_app
from api.auth import get_current_actor
from api.routes.company_profiles import get_company_profile_service
from api.schemas import CompanyProfileData
from construction_rate_limit_fixture import with_local_construction_rate_limit
from handlers.construction import construction_company_profile as handler
from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.platform_company_profiles import TenantCompanyProfileService
from ui.construction_company_profile import LABELS, profile_text


@pytest.fixture
def company_http():
    c = NS(tenant=uuid4(), user=uuid4())
    c.actor = NS(user_id=c.user, tenant_id=c.tenant, status="active", permissions=())
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    c.row = NS(
        id=uuid4(), tenant_id=c.tenant, legal_name="COMPANY_PRIVATE_LEGAL_NAME",
        commercial_name="COMPANY_PRIVATE_COMMERCIAL_NAME",
        registered_address_raw="COMPANY_PRIVATE_REGISTERED_ADDRESS",
        operational_address_raw="COMPANY_PRIVATE_OPERATING_ADDRESS",
        phone="+12025550123", email="private-profile@example.test",
        website="https://example.test", signatory_name="COMPANY_PRIVATE_SIGNATORY",
        signatory_position="Director", logo_file_id=None,
        created_at=now, updated_at=now, row_version=1,
    )
    c.data_item = CompanyProfileData.model_validate(vars(c.row)).model_dump(mode="json")
    c.session = NS(add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    c.repo = NS(get_for_tenant=AsyncMock(return_value=c.row))
    c.service = TenantCompanyProfileService(
        session=c.session, repository=c.repo,
        event_repository=object(), file_repository=object(),
    )
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_company_profile_service] = lambda: c.service
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_real_platform_profile_get_accepts_member_and_nullable_profile(company_http, missing):
    c = company_http
    if missing:
        c.repo.get_for_tenant.return_value = None
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
    ) as http:
        result = await ConstructionApiClient(http=http).company_profile(
            session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
        )
    if missing:
        assert result.data is None
    else:
        assert result.data["tenant_id"] == str(c.tenant)
        assert result.data["legal_name"] == c.row.legal_name
    c.repo.get_for_tenant.assert_awaited_once_with(tenant_id=c.tenant)
    c.session.add.assert_not_called()
    c.session.flush.assert_not_awaited()
    c.session.commit.assert_not_awaited()
    c.session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "version", "timestamp", "private"])
async def test_transport_rejects_malformed_or_foreign_profile(company_http, failure):
    c = company_http
    data = deepcopy(c.data_item)
    if failure == "tenant":
        data["tenant_id"] = str(uuid4())
    elif failure == "version":
        data["row_version"] = True
    elif failure == "timestamp":
        data["updated_at"] = "2026-10-26T12:00:00"
    else:
        data["private"] = "hidden"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": data, "meta": {}, "request_id": "company-local"},
        )),
    ) as http:
        with pytest.raises(ConstructionApiClientError) as caught:
            await ConstructionApiClient(http=http).company_profile(
                session=PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
            )
    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_nullable_company_response_does_not_allow_null_for_existing_me_operation(company_http):
    c = company_http
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": None, "meta": {}, "request_id": "local"},
        )),
    ) as http:
        api = ConstructionApiClient(http=http)
        session = PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test"))
        with pytest.raises(ConstructionApiClientError):
            await api.me(session=session)
        with pytest.raises(ValueError):
            await api._request("GET", "me", session=session, allow_null=True)


@pytest.fixture
def screen(company_http, monkeypatch):
    c = company_http
    c.state_data = {
        handler.CONTEXT: {"telegram_user_id": 123, "tenant_id": str(c.tenant)},
        "last_menu_message_id": 1,
    }
    async def update(**values):
        c.state_data.update(values)
    c.state = NS(
        get_data=AsyncMock(side_effect=lambda: c.state_data),
        update_data=AsyncMock(side_effect=update),
        get_state=AsyncMock(return_value=None),
    )
    c.callback = NS(
        data="CP:open", from_user=NS(id=123, is_bot=False, language_code="uk"),
        answer=AsyncMock(),
        message=NS(message_id=1, chat=NS(id=123, type="private"), answer=AsyncMock()),
    )
    c.result = {
        "item": deepcopy(c.data_item), "actor_user_id": str(c.user),
        "organization": "Organization", "language": "uk",
    }
    c.ui_service = NS(company_profile=AsyncMock(side_effect=lambda **kwargs: deepcopy(c.result)))
    @asynccontextmanager
    async def entry(**kwargs):
        yield c.ui_service
    monkeypatch.setattr(handler, "construction_bot_api_entry", entry)
    c.sent = AsyncMock(return_value=NS(message_id=1))
    monkeypatch.setattr(handler, "edit_or_replace_menu_message", c.sent)
    return c


async def refresh(c):
    c.callback.message.message_id = c.state_data["last_menu_message_id"]
    c.callback.data = "CP:refresh:" + c.state_data[handler.VIEW]["nonce"]
    await handler.company_callback(c.callback, c.state)


@pytest.mark.asyncio
async def test_company_card_renders_fresh_profile_and_keeps_only_selectors(screen):
    c = screen
    await handler.company_callback(c.callback, c.state)
    assert c.row.legal_name in c.sent.await_args.kwargs["text"]
    assert c.row.phone not in repr(c.state_data)
    assert c.row.legal_name not in repr(c.state_data)
    assert not {"item", "legal_name", "phone", "email", "signatory_name"} & set(c.state_data[handler.VIEW])
    c.result["item"]["commercial_name"] = "CHANGED_COMPANY_NAME"
    await refresh(c)
    assert "CHANGED_COMPANY_NAME" in c.sent.await_args.kwargs["text"]
    assert c.ui_service.company_profile.await_args.kwargs["expected_user_id"] == c.user


@pytest.mark.asyncio
async def test_absent_profile_is_a_normal_read_state(screen):
    c = screen
    c.result["item"] = None
    await handler.company_callback(c.callback, c.state)
    assert "ще не заповнено" in c.sent.await_args.kwargs["text"]
    assert c.state_data[handler.VIEW]["actor_user_id"] == str(c.user)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "menu", "sender", "nonce"])
async def test_stale_or_foreign_profile_refresh_has_no_api_read(screen, failure):
    c = screen
    await handler.company_callback(c.callback, c.state)
    if failure == "tenant":
        c.state_data[handler.CONTEXT]["tenant_id"] = str(uuid4())
    elif failure == "menu":
        c.state_data[handler.VIEW]["menu_id"] = 99
    elif failure == "sender":
        c.callback.from_user.id = c.callback.message.chat.id = 999
    else:
        c.callback.data = "CP:refresh:obsolete"
        await handler.company_callback(c.callback, c.state)
        assert c.ui_service.company_profile.await_count == 1
        return
    await refresh(c)
    assert c.ui_service.company_profile.await_count == 1


@pytest.mark.asyncio
async def test_subject_change_stops_before_profile_get(company_http):
    c = company_http
    api = NS(company_profile=AsyncMock())
    entry = ConstructionBotApiEntry(api=NS(construction=api), context=None, sessions=None)
    entry._presentation = AsyncMock(return_value=(
        PlatformApiClientSession(c.user, c.tenant, SecretStr("local-test")),
        {"name": "Organization", "language": "uk"},
    ))
    with pytest.raises(ConstructionApiClientError):
        await entry.company_profile(
            telegram_user_id=123, tenant_id=c.tenant, expected_user_id=uuid4(),
        )
    api.company_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_denied_refresh_clears_read_view_without_cached_profile(screen):
    c = screen
    await handler.company_callback(c.callback, c.state)
    c.ui_service.company_profile.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )
    await refresh(c)
    assert c.state_data[handler.VIEW] == {}
    assert c.row.legal_name not in c.sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_navigation_invalidates_company_selectors_only(screen):
    c = screen
    await handler.company_callback(c.callback, c.state)
    c.state_data["construction_project_replace_attempt"] = {"status": "pending"}
    c.callback.data = "CS_HOME"
    await handler.company_navigation_middleware(
        AsyncMock(), c.callback, {"state": c.state},
    )
    assert c.state_data[handler.VIEW] == {}
    assert c.state_data["construction_project_replace_attempt"]["status"] == "pending"


def test_presentation_is_bounded_and_all_labels_have_seven_languages(company_http):
    c = company_http
    data = deepcopy(c.data_item)
    for field in (
        "legal_name", "commercial_name", "registered_address_raw",
        "operational_address_raw", "signatory_name", "signatory_position",
    ):
        data[field] = "X" * 10000
    text = profile_text({"item": data, "organization": "Organization"}, "uk")
    assert len(text) < 3800
    assert all(len(values) == 7 for values in LABELS.values())
