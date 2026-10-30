"""Local ASGI catalog integration; no external services or database writes."""
import base64
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import (
    get_api_request_rate_limit_service,
    get_tenant_module_access_service, get_tenant_suite_access_service,
)
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from api.routes import construction_project_catalog as routes
from api.construction_project_catalog_cursors import (
    encode_project_catalog_cursor, decode_project_catalog_cursor,
)
from services.construction.construction_project_catalog_read import ConstructionProjectCatalogReadCursor
from services.construction.construction_projects import ConstructionProjectValidationError
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.module_access import SuiteDisabledError, ModuleDisabledError
from construction_rate_limit_fixture import with_local_construction_rate_limit
from test_construction_project_catalog_read import catalog_case, no_writes, project_scope
from test_construction_1a_client_projects_read_service import service_case
from test_construction_01_project_read_http import walk_routes

PATH = "/api/v1/construction/projects"


@pytest.fixture
def catalog_http(catalog_case):
    c = catalog_case
    c.suite = NS(require_enabled=AsyncMock())
    c.module = NS(require_enabled=AsyncMock())
    c.rate = NS(ensure_authenticated_user_allowed=AsyncMock())
    c.app = with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor] = lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context] = lambda: c.context
    c.app.dependency_overrides[get_construction_scoped_session] = lambda: c.session
    c.app.dependency_overrides[get_tenant_suite_access_service] = lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service] = lambda: c.module
    c.app.dependency_overrides[get_api_request_rate_limit_service] = lambda: c.rate
    c.app.dependency_overrides[routes.get_project_catalog_read_service] = lambda: c.catalog
    return c


async def get(c, params=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=c.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.get(
            PATH, params=params,
            headers={"X-Request-ID": "project-catalog", "X-Tenant-ID": str(uuid4())},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["tenant", "project"])
async def test_registered_catalog_uses_actor_context_real_service_and_one_shared_admission(catalog_http, scope):
    c = catalog_http
    c.row.client_id = None
    if scope == "project":
        project_scope(c)
    response = await get(c)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"][0]["id"] == str(c.row.id)
    assert body["data"][0]["tenant_id"] == str(c.tenant)
    assert body["data"][0]["client_id"] is None
    assert body["meta"] == {"pagination": {"next_cursor": None}}
    assert body["request_id"] == response.headers["X-Request-ID"] == "project-catalog"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "private_internal" not in response.text and "deleted_at" not in body["data"][0]
    args = c.catalog_repo.list_projects.await_args.kwargs
    assert args["tenant_id"] == c.tenant and args["limit"] == 20
    assert args["allowed_project_ids"] == ((c.row.id,) if scope == "project" else None)
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
async def test_two_pages_continue_after_last_public_item(catalog_http):
    c = catalog_http
    rows = []
    for number in (30, 20, 10):
        row = deepcopy(c.row)
        row.id = UUID(int=number)
        rows.append(row)
    async def query(**options):
        after = options["after"]
        return [
            row for row in rows
            if after is None or row.id < after.project_id
        ][:options["limit"] + 1]
    c.catalog_repo.list_projects.side_effect = query
    first = await get(c, {"limit": 2})
    assert first.status_code == 200, first.text
    token = first.json()["meta"]["pagination"]["next_cursor"]
    assert isinstance(token, str)
    second = await get(c, {"limit": 2, "cursor": token})
    assert second.status_code == 200, second.text
    identifiers = [item["id"] for item in first.json()["data"] + second.json()["data"]]
    assert identifiers == [str(row.id) for row in rows]
    assert second.json()["meta"]["pagination"]["next_cursor"] is None
    assert c.rate.ensure_authenticated_user_allowed.await_count == 2
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "anonymous", "inactive", "permission", "document", "foreign_context",
    "suite", "module", "rate", "membership", "revoked", "expired",
])
async def test_gates_deny_before_catalog_query(catalog_http, failure):
    c = catalog_http
    expected = 403
    if failure == "anonymous":
        c.app.dependency_overrides.pop(get_current_actor)
        expected = 401
    elif failure == "inactive":
        c.actor.status = "blocked"
    elif failure == "permission":
        c.context = replace(c.context, permissions=())
    elif failure == "document":
        c.context = ConstructionAccessContext(
            c.user, c.tenant, ("OBSERVER",), ("construction.projects.read",),
            (ConstructionGrantContext("OBSERVER", "document", c.row.id),),
        )
    elif failure == "foreign_context":
        c.context = replace(c.context, tenant_id=uuid4())
    elif failure == "suite":
        c.suite.require_enabled.side_effect = SuiteDisabledError()
    elif failure == "module":
        c.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "rate":
        c.rate.ensure_authenticated_user_allowed.side_effect = ApiRequestRateLimitExceededError()
        expected = 429
    elif failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    else:
        c.grant.expires_at = c.now
    response = await get(c)
    assert response.status_code == expected, response.text
    assert "data" not in response.json() and c.row.name not in response.text
    c.catalog_repo.list_projects.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"limit": "true"},
    {"cursor": ""}, {"cursor": "bad"},
    {"tenant_id": "caller-tenant"}, {"user_id": "caller-user"},
    [("limit", "20"), ("limit", "21")],
])
async def test_invalid_or_identity_query_fields_are_not_accepted(catalog_http, params):
    c = catalog_http
    response = await get(c, params)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    c.catalog_repo.list_projects.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", ["user", "tenant"])
async def test_cursor_is_bound_to_current_actor_and_tenant(catalog_http, foreign):
    c = catalog_http
    tenant = uuid4() if foreign == "tenant" else c.tenant
    user = uuid4() if foreign == "user" else c.user
    token = encode_project_catalog_cursor(
        ConstructionProjectCatalogReadCursor(tenant, user, c.now, c.row.id),
        tenant_id=tenant, user_id=user,
    )
    response = await get(c, {"cursor": token})
    assert response.status_code == 422
    c.catalog_repo.list_projects.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
async def test_valid_cursor_does_not_preserve_revoked_authority(catalog_http):
    c = catalog_http
    token = encode_project_catalog_cursor(
        ConstructionProjectCatalogReadCursor(c.tenant, c.user, c.now, c.row.id),
        tenant_id=c.tenant, user_id=c.user,
    )
    c.grant.status = "revoked"
    response = await get(c, {"cursor": token})
    assert response.status_code == 403
    c.catalog_repo.list_projects.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
async def test_query_failure_is_sanitized_without_own_commit_or_rollback(catalog_http):
    c = catalog_http
    c.catalog_repo.list_projects.side_effect = RuntimeError("private storage diagnostic")
    response = await get(c)
    assert response.status_code == 500
    assert "private storage diagnostic" not in response.text
    assert c.row.name not in response.text
    no_writes(c)


@pytest.mark.asyncio
async def test_access_loss_after_query_prevents_http_publication(catalog_http):
    c = catalog_http
    async def query(**options):
        c.grant.status = "revoked"
        return [c.row]
    c.catalog_repo.list_projects.side_effect = query
    response = await get(c)
    assert response.status_code == 403 and "data" not in response.json()
    no_writes(c)


def test_cursor_roundtrip_is_canonical_and_not_interchangeable_with_other_lists(catalog_http):
    c = catalog_http
    position = ConstructionProjectCatalogReadCursor(c.tenant, c.user, c.now, c.row.id)
    token = encode_project_catalog_cursor(position, tenant_id=c.tenant, user_id=c.user)
    assert decode_project_catalog_cursor(token, tenant_id=c.tenant, user_id=c.user) == position
    raw = base64.urlsafe_b64decode(token + "=" * ((-len(token)) % 4))
    changed = raw.replace(b"construction.projects.catalog", b"construction.crm.leads")
    wrong_kind = base64.urlsafe_b64encode(changed).decode().rstrip("=")
    for bad in (token + "=", wrong_kind, "x" * 1025):
        with pytest.raises(ConstructionProjectValidationError):
            decode_project_catalog_cursor(bad, tenant_id=c.tenant, user_id=c.user)


def test_openapi_and_single_registration_preserve_project_card_and_creation(catalog_http):
    c = catalog_http
    paths = c.app.openapi()["paths"]
    assert "get" in paths[PATH] and "post" in paths[PATH]
    assert "get" in paths[PATH + "/{id}"]
    operation = paths[PATH]["get"]
    parameters = {
        value["name"]: value for value in operation["parameters"]
        if value["in"] == "query"
    }
    assert set(parameters) == {"limit", "cursor"}
    assert parameters["limit"]["schema"]["default"] == 20
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert {"401", "403", "422", "429", "500"} <= set(operation["responses"])
    matches = [
        route for path, route in walk_routes(c.app)
        if path == PATH and "GET" in getattr(route, "methods", ())
    ]
    assert len(matches) == 1 and matches[0].endpoint is routes.list_project_catalog
