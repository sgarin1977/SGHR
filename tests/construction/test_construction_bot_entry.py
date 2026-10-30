from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

import handlers.construction.construction_suite as handler
from services.api_identity import ApiIdentityAccessError


















@pytest.mark.asyncio
async def test_membership_discovery_query_excludes_foreign_and_global_only_roles():
    from database.repositories.platform_organizations import PlatformOrganizationsRepository
    result = Mock()
    result.mappings.return_value.all.return_value = []
    session = NS(execute=AsyncMock(return_value=result))
    user, after = uuid4(), uuid4()
    await PlatformOrganizationsRepository(session).list_organizations(user_id=user, after=after)
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
    assert 'users.status =' in sql and 'user_roles.status =' in sql and 'tenants.status =' in sql
    assert 'users.tenant_id = tenants.id' in sql and 'user_roles.tenant_id = tenants.id' in sql
    assert 'user_roles.user_id = users.id' in sql and str(user) in sql
    assert 'ORDER BY tenants.id' in sql and 'LIMIT 11' in sql and 'tenants.id >' in sql


@pytest.fixture
def transport(monkeypatch):
    c = NS(tenant=uuid4(), service=NS(home=AsyncMock(), organizations=AsyncMock(), leads=AsyncMock(), lead=AsyncMock()))
    c.callback = NS(from_user=NS(id=123, language_code='uk'), message=NS(chat=NS(id=123, type='private')),
        answer=AsyncMock(), data='CS_HOME')
    c.data = {handler.STATE_KEY: dict(telegram_user_id=123, tenant_id=str(c.tenant))}
    async def update(**values): c.data.update(values)
    c.state = NS(get_data=AsyncMock(side_effect=lambda: c.data), update_data=AsyncMock(side_effect=update))
    c.builder = Mock(return_value=c.service)
    @asynccontextmanager
    async def api_entry(*, bot, event_update):
        c.builder(bot=bot, event_update=event_update)
        yield c.service
    monkeypatch.setattr(handler, 'construction_bot_api_entry', api_entry)
    c.render = AsyncMock()
    monkeypatch.setattr(handler, 'render', c.render)
    return c


@pytest.mark.asyncio
async def test_group_callback_cannot_read_or_render_construction_data(transport):
    c = transport
    c.callback.message.chat.type = 'group'
    await handler.construction_callback(c.callback, c.state)
    c.builder.assert_not_called()
    c.render.assert_not_called()
    assert c.callback.answer.await_args.kwargs['show_alert'] is True


@pytest.mark.asyncio
async def test_another_telegram_identity_cannot_reuse_stored_context(transport):
    c = transport
    c.data[handler.STATE_KEY]['telegram_user_id'] = 999
    await handler.construction_callback(c.callback, c.state)
    c.service.home.assert_not_called()
    assert c.data[handler.STATE_KEY] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("capabilities,sections", [
    ({}, []),
    ({"can_read_leads": True}, ["CM:open"]),
    ({"can_read_clients": True}, ["CM:open"]),
    ({"can_read_projects": True}, ["PL:open"]),
    ({"can_manage_team": True}, ["CT:open"]),
    ({
        "can_read_leads": True,
        "can_read_clients": True,
        "can_read_projects": True,
        "can_manage_team": True,
    }, ["CT:open", "PL:open", "CM:open"]),
])
async def test_home_renders_only_available_working_section(
    transport, capabilities, sections,
):
    c = transport
    # An OWNER label alone must not enable an absent server capability.
    c.service.home.return_value = {
        "language": "uk", "name": "Organization",
        "currency": "EUR", "roles": ("OWNER",),
        **capabilities,
    }
    await handler.construction_callback(c.callback, c.state)
    c.render.assert_awaited_once()
    rows = c.render.await_args.args[3]
    callbacks = [button.callback_data for row in rows for button in row]
    assert callbacks == sections + [
        "TGB:open", "CP:open", "CS_ORGS", "M_SUITES",
    ]
    c.service.home.assert_awaited_once_with(
        telegram_user_id=123, tenant_id=c.tenant,
    )
    c.service.leads.assert_not_awaited()
    c.service.organizations.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_page_uses_server_cursor_and_rechecks_domain_access(transport):
    c = transport
    c.callback.data = 'CS_LEADS_NEXT'
    c.data[handler.STATE_KEY]['leads_cursor'] = 'server-stored-position'
    c.service.leads.return_value = dict(language='uk', organization='Organization', items=(), next_cursor=None)
    await handler.construction_callback(c.callback, c.state)
    c.service.leads.assert_awaited_once_with(telegram_user_id=123, tenant_id=c.tenant, cursor='server-stored-position')
    assert c.data[handler.STATE_KEY]['leads_cursor'] is None


@pytest.mark.asyncio
async def test_domain_denial_is_safe_and_clears_context(transport):
    c = transport
    c.service.home.side_effect = ApiIdentityAccessError('private organization information')
    await handler.construction_callback(c.callback, c.state)
    assert 'private organization information' not in c.render.await_args.args[2]
    assert c.data[handler.STATE_KEY] == {}

# construction_explicit_organization_selection_v1
@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("previous_selection", [False, True])
async def test_entry_always_shows_selector_and_create_without_opening_tenant(
    transport, count, previous_selection,
):
    from services.construction.construction_api_client import ConstructionApiClientError
    c = transport
    if not previous_selection:
        c.data[handler.STATE_KEY] = {"telegram_user_id": 123}
    items = [
        {"id": str(c.tenant), "name": "SGHR"},
        {"id": str(uuid4()), "name": "Second organization"},
    ][:count]
    c.service.organizations.return_value = {
        "language": "uk", "items": items, "next_after": None,
    }
    c.service.home.side_effect = ConstructionApiClientError(
        status_code=403, code="forbidden",
    )

    await handler.open_construction(c.callback, c.state)

    c.service.organizations.assert_awaited_once_with(
        telegram_user_id=123, after=None,
    )
    c.service.home.assert_not_awaited()
    c.render.assert_awaited_once()
    rows = c.render.await_args.args[3]
    buttons = [button for row in rows for button in row]
    callbacks = [button.callback_data for button in buttons]

    expected = {
        "CS_ORG:" + item["id"].replace("-", "") for item in items
    }
    assert {
        value for value in callbacks if value.startswith("CS_ORG:")
    } == expected
    assert sum(value.startswith("OC:new:") for value in callbacks) == 1
    assert callbacks[-1] == "M_SUITES"
    assert "tenant_id" not in c.data[handler.STATE_KEY]
    assert c.data[handler.STATE_KEY]["organization_creation_nonce"]
    if count:
        assert any(
            button.text == "SGHR"
            and button.callback_data.startswith("CS_ORG:")
            for button in buttons
        )


@pytest.mark.asyncio
async def test_selector_keeps_server_pagination_without_automatic_entry(transport):
    c = transport
    cursor = uuid4()
    c.service.organizations.return_value = {
        "language": "uk",
        "items": [{"id": str(c.tenant), "name": "SGHR"}],
        "next_after": str(cursor),
    }
    await handler.open_construction(c.callback, c.state)
    callbacks = [
        button.callback_data
        for row in c.render.await_args.args[3] for button in row
    ]
    assert "CS_ORGS_NEXT" in callbacks

    c.service.organizations.return_value = {
        "language": "uk", "items": [], "next_after": None,
    }
    c.callback.data = "CS_ORGS_NEXT"
    await handler.construction_callback(c.callback, c.state)

    assert c.service.organizations.await_args.kwargs == {
        "telegram_user_id": 123, "after": cursor,
    }
    c.service.home.assert_not_awaited()
    assert "tenant_id" not in c.data[handler.STATE_KEY]
