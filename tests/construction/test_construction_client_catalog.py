"""Client catalog isolation, fresh CRM policy, HTTP and actual SQL pagination."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session

from database.models import ConstructionClient
from database.repositories.construction.construction_client_catalog import (
    ConstructionClientCatalogRepository, ConstructionClientCatalogCursor,
)
from services.construction.construction_client_catalog_read import (
    ConstructionClientCatalogReadService, ConstructionClientCatalogReadCursor,
    ConstructionClientCatalogReadError,
)
from services.construction.construction_clients import ConstructionClientValidationError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.module_access import SuiteDisabledError, ModuleDisabledError
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_suite_access_service, get_tenant_module_access_service,
)
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from api.routes import construction_client_catalog as routes
from api.construction_client_catalog_cursors import (
    encode_client_catalog_cursor, decode_client_catalog_cursor,
)
from construction_rate_limit_fixture import with_local_construction_rate_limit
from test_construction_1a_client_card_read_service import card_case, no_writes
from test_construction_01_project_read_http import walk_routes

PATH = "/api/v1/construction/clients"


@pytest.fixture
def client_catalog_case(card_case):
    c = card_case
    c.catalog_repo = NS(session=c.session, list_clients=AsyncMock(return_value=[c.client_row]))
    c.catalog = ConstructionClientCatalogReadService(
        session=c.session, repository=c.catalog_repo, access_repository=c.access,
        now_provider=lambda: c.now,
    )
    return c


async def read(c, **changes):
    return await c.catalog.list_clients(**{
        "actor": c.actor, "access_context": c.context, **changes,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["person", "company"])
async def test_catalog_reuses_client_fields_without_loading_related_entities(client_catalog_case, kind):
    c = client_catalog_case
    c.client_row.client_type = kind
    page = await read(c)
    assert page.items[0]["id"] == str(c.client)
    assert page.items[0]["client_type"] == kind
    assert set(page.items[0]) == set(routes.ClientReadResponse.model_fields)
    assert "private" not in page.items[0] and "projects" not in page.items[0]
    c.clients.get_active_client.assert_not_awaited()
    for repo in c.repositories.values():
        for name, value in vars(repo).items():
            if isinstance(value, AsyncMock):
                value.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["project", "document"])
async def test_scoped_grant_or_identity_link_cannot_read_tenant_client_catalog(client_catalog_case, scope):
    c = client_catalog_case
    role = "PROJECT_MANAGER" if scope == "project" else "OBSERVER"
    c.context = ConstructionAccessContext(
        c.user, c.tenant, (role,), ("construction.projects.read",),
        (ConstructionGrantContext(role, scope, c.project.id),),
    )
    c.client_row.linked_user_id = c.user
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    c.catalog_repo.list_clients.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired"])
async def test_fresh_access_is_required_before_query(client_catalog_case, failure):
    c = client_catalog_case
    if failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    else:
        c.grant.expires_at = c.now
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    c.catalog_repo.list_clients.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
async def test_access_loss_during_query_prevents_publication(client_catalog_case):
    c = client_catalog_case
    async def query(**kwargs):
        c.grant.status = "revoked"
        return [c.client_row]
    c.catalog_repo.list_clients.side_effect = query
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign", "deleted", "duplicate", "version"])
async def test_foreign_deleted_or_malformed_rows_are_rejected(client_catalog_case, failure):
    c = client_catalog_case
    if failure == "foreign":
        c.client_row.tenant_id = uuid4()
    elif failure == "deleted":
        c.client_row.deleted_at = c.now
    elif failure == "duplicate":
        c.catalog_repo.list_clients.return_value = [c.client_row, deepcopy(c.client_row)]
    else:
        c.client_row.row_version = True
    with pytest.raises(ConstructionClientCatalogReadError):
        await read(c)
    no_writes(c)


@pytest.fixture
def client_catalog_http(client_catalog_case):
    c = client_catalog_case
    c.suite, c.module = NS(require_enabled=AsyncMock()), NS(require_enabled=AsyncMock())
    c.rate = NS(ensure_authenticated_user_allowed=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rate
    c.app.dependency_overrides[routes.get_client_catalog_read_service] = lambda: c.catalog
    return c


async def get(c, params=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as http:
        return await http.get(
            PATH, params=params,
            headers={"X-Request-ID": "client-catalog", "X-Tenant-ID": str(uuid4())},
        )


@pytest.mark.asyncio
async def test_registered_http_returns_existing_client_projection_and_one_rate_admission(client_catalog_http):
    c = client_catalog_http
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"][0]["id"] == str(c.client)
    assert body["data"][0]["tenant_id"] == str(c.tenant)
    assert body["meta"] == {"pagination": {"next_cursor": None}}
    assert body["request_id"] == response.headers["X-Request-ID"] == "client-catalog"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "private" not in body["data"][0] and "deleted_at" not in body["data"][0]
    c.catalog_repo.list_clients.assert_awaited_once_with(
        tenant_id=c.tenant, limit=20, after=None,
    )
    c.rate.ensure_authenticated_user_allowed.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.user,
    )
    c.suite.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, suite_code="construction",
    )
    c.module.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, module_code="construction.core",
    )
    no_writes(c)


@pytest.mark.asyncio
async def test_two_http_pages_follow_last_public_item(client_catalog_http):
    c = client_catalog_http
    rows = []
    for number in (30, 20, 10):
        row = deepcopy(c.client_row)
        row.id = UUID(int=number)
        rows.append(row)
    async def query(**kwargs):
        after = kwargs["after"]
        return [
            row for row in rows if after is None or row.id < after.client_id
        ][:kwargs["limit"] + 1]
    c.catalog_repo.list_clients.side_effect = query
    first = await get(c, {"limit": 2})
    assert first.status_code == 200, first.text
    token = first.json()["meta"]["pagination"]["next_cursor"]
    second = await get(c, {"limit": 2, "cursor": token})
    assert second.status_code == 200, second.text
    assert [item["id"] for item in first.json()["data"] + second.json()["data"]] == [
        str(row.id) for row in rows
    ]
    assert second.json()["meta"]["pagination"]["next_cursor"] is None
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "anonymous", "inactive", "permission", "project", "suite", "module", "rate",
])
async def test_http_gates_prevent_catalog_query(client_catalog_http, failure):
    c = client_catalog_http
    expected = 403
    if failure == "anonymous":
        c.app.dependency_overrides.pop(get_current_actor)
        expected = 401
    elif failure == "inactive":
        c.actor.status = "blocked"
    elif failure == "permission":
        c.context = replace(c.context, permissions=())
    elif failure == "project":
        c.context = ConstructionAccessContext(
            c.user, c.tenant, ("PROJECT_MANAGER",), ("construction.projects.read",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project.id),),
        )
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    else:
        c.rate.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected = 429
    response = await get(c)
    assert response.status_code == expected, response.text
    assert "data" not in response.json()
    c.catalog_repo.list_clients.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"limit": "true"}, {"cursor": ""},
    {"cursor": "bad"}, {"tenant_id": "caller-tenant"}, {"user_id": "caller-user"},
    [("limit", "20"), ("limit", "21")],
])
async def test_invalid_or_identity_query_is_rejected(client_catalog_http, params):
    c = client_catalog_http
    response = await get(c, params)
    assert response.status_code == 422
    c.catalog_repo.list_clients.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", ["tenant", "user"])
async def test_cursor_is_bound_to_actor_and_tenant(client_catalog_http, foreign):
    c = client_catalog_http
    tenant = uuid4() if foreign == "tenant" else c.tenant
    user = uuid4() if foreign == "user" else c.user
    token = encode_client_catalog_cursor(
        ConstructionClientCatalogReadCursor(tenant, user, c.now, c.client),
        tenant_id=tenant, user_id=user,
    )
    assert (await get(c, {"cursor": token})).status_code == 422
    c.catalog_repo.list_clients.assert_not_awaited()
    no_writes(c)


def test_cursor_roundtrip_and_catalog_kind_separation(client_catalog_http):
    c = client_catalog_http
    position = ConstructionClientCatalogReadCursor(c.tenant, c.user, c.now, c.client)
    token = encode_client_catalog_cursor(position, tenant_id=c.tenant, user_id=c.user)
    assert decode_client_catalog_cursor(token, tenant_id=c.tenant, user_id=c.user) == position
    from api.construction_project_catalog_cursors import decode_project_catalog_cursor
    from services.construction.construction_projects import ConstructionProjectValidationError
    with pytest.raises(ConstructionProjectValidationError):
        decode_project_catalog_cursor(token, tenant_id=c.tenant, user_id=c.user)
    with pytest.raises(ConstructionClientValidationError):
        decode_client_catalog_cursor(token + "=", tenant_id=c.tenant, user_id=c.user)


def test_openapi_preserves_existing_client_card_and_creation(client_catalog_http):
    paths = client_catalog_http.app.openapi()["paths"]
    assert "get" in paths[PATH] and "post" in paths[PATH]
    assert "get" in paths[PATH + "/{id}"]
    operation = paths[PATH]["get"]
    query = {p["name"] for p in operation["parameters"] if p["in"] == "query"}
    assert query == {"limit", "cursor"}
    matches = [
        route for path, route in walk_routes(client_catalog_http.app)
        if path == PATH and "GET" in getattr(route, "methods", ())
    ]
    assert len(matches) == 1 and matches[0].endpoint is routes.list_client_catalog


@pytest.mark.asyncio
async def test_real_sql_pagination_includes_clients_without_projects_or_leads(client_catalog_case):
    c = client_catalog_case
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def enforce(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    foreign = uuid4()
    identifiers = [UUID(int=value) for value in range(1, 26)]
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE tenants(id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE users(id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql(
            "INSERT INTO tenants VALUES(?),(?)", (c.tenant.hex, foreign.hex),
        )
        ConstructionClient.__table__.create(connection)
        for index, identifier in enumerate(identifiers):
            connection.execute(insert(ConstructionClient.__table__).values(
                id=identifier, tenant_id=c.tenant, display_name=f"Client {index}",
                client_type="person" if index % 2 else "company",
                created_at=c.now, updated_at=c.now, row_version=1,
            ))
        for identifier, tenant, deleted in (
            (UUID(int=100), c.tenant, c.now),
            (UUID(int=101), foreign, None),
        ):
            connection.execute(insert(ConstructionClient.__table__).values(
                id=identifier, tenant_id=tenant, display_name="Hidden",
                client_type="person", created_at=c.now, updated_at=c.now,
                row_version=1, deleted_at=deleted,
            ))
    sync = Session(engine)
    flushes = []
    event.listen(sync, "before_flush", lambda *args: flushes.append(True))
    async def execute(statement):
        return sync.execute(statement)
    session = NS(execute=execute)
    repository = ConstructionClientCatalogRepository(session)
    try:
        received, after = [], None
        while True:
            rows = await repository.list_clients(tenant_id=c.tenant, limit=10, after=after)
            received.extend(row.id for row in rows[:10])
            if len(rows) <= 10:
                break
            after = ConstructionClientCatalogCursor(c.tenant, c.now, rows[9].id)
        assert received == list(reversed(identifiers))
        assert flushes == []
    finally:
        sync.close()
        engine.dispose()
