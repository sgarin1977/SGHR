"""Internal project catalog authorization, scope and actual SQL pagination."""
from copy import deepcopy
from datetime import timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert

from database.repositories.construction.construction_project_catalog import (
    ConstructionProjectCatalogRepository, ConstructionProjectCatalogCursor,
)
from services.construction.construction_project_catalog_read import (
    ConstructionProjectCatalogReadService, ConstructionProjectCatalogReadCursor,
    ConstructionProjectCatalogReadError,
)
from services.construction.construction_projects import ConstructionProjectValidationError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_1a_client_projects_read_service import service_case
from test_construction_1a_client_projects_repository import (
    repository_case, real_case, project, TABLE,
)


@pytest.fixture
def catalog_case(service_case):
    c = service_case
    c.catalog_repo = NS(
        session=c.session, list_projects=AsyncMock(return_value=[c.row]),
    )
    c.catalog = ConstructionProjectCatalogReadService(
        session=c.session, repository=c.catalog_repo, access_repository=c.access,
        now_provider=lambda: c.now,
    )
    return c


async def read(c, **changes):
    return await c.catalog.list_projects(**{
        "actor": c.actor, "access_context": c.context, **changes,
    })


def no_writes(c):
    c.session.add.assert_not_called()
    c.session.flush.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


def project_scope(c, identifier=None):
    identifier = identifier or c.row.id
    c.grant.role, c.grant.scope_type, c.grant.scope_id = "PROJECT_MANAGER", "project", identifier
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("PROJECT_MANAGER",), ("construction.projects.read",),
        (ConstructionGrantContext("PROJECT_MANAGER", "project", identifier),),
    )


@pytest.mark.asyncio
async def test_tenant_scope_lists_standalone_projects_without_client_gate(catalog_case):
    c = catalog_case
    c.row.client_id = None
    page = await read(c)
    assert page.items[0]["id"] == str(c.row.id)
    assert page.items[0]["client_id"] is None
    assert "private_internal" not in page.items[0] and "deleted_at" not in page.items[0]
    c.catalog_repo.list_projects.assert_awaited_once_with(
        tenant_id=c.tenant, allowed_project_ids=None, limit=20, after=None,
    )
    c.clients.get_active_client.assert_not_called()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "planning", "active", "completed", "cancelled"])
async def test_all_existing_workflow_states_are_readable(catalog_case, status):
    c = catalog_case
    c.row.status = status
    assert (await read(c)).items[0]["status"] == status
    no_writes(c)


@pytest.mark.asyncio
async def test_project_scope_is_sent_to_sql_before_pagination(catalog_case):
    c = catalog_case
    project_scope(c)
    assert (await read(c)).items[0]["id"] == str(c.row.id)
    assert c.catalog_repo.list_projects.await_args.kwargs["allowed_project_ids"] == (c.row.id,)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["membership", "revoked", "expired"])
async def test_supplied_context_does_not_replace_fresh_authorization(catalog_case, failure):
    c = catalog_case
    if failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    else:
        c.grant.expires_at = c.now
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    c.catalog_repo.list_projects.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
async def test_document_access_and_creator_responsibility_do_not_grant_project_catalog(catalog_case):
    c = catalog_case
    c.grant.role, c.grant.scope_type, c.grant.scope_id = "OBSERVER", "document", c.row.id
    c.context = ConstructionAccessContext(
        c.user, c.tenant, ("OBSERVER",), ("construction.projects.read",),
        (ConstructionGrantContext("OBSERVER", "document", c.row.id),),
    )
    assert c.row.created_by == c.user and c.row.responsible_user_id == c.user
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    c.catalog_repo.list_projects.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["grant", "membership"])
async def test_access_loss_during_query_prevents_publication(catalog_case, failure):
    c = catalog_case
    async def query(**kwargs):
        if failure == "grant":
            c.grant.status = "revoked"
        else:
            c.access.has_active_platform_membership.return_value = False
        return [c.row]
    c.catalog_repo.list_projects.side_effect = query
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign", "deleted", "duplicate", "order", "version"])
async def test_malformed_repository_page_is_rejected(catalog_case, failure):
    c = catalog_case
    if failure == "foreign":
        c.row.tenant_id = uuid4()
    elif failure == "deleted":
        c.row.deleted_at = c.now
    elif failure == "duplicate":
        c.catalog_repo.list_projects.return_value = [c.row, deepcopy(c.row)]
    elif failure == "order":
        newer = deepcopy(c.row)
        newer.id, newer.updated_at = uuid4(), c.now + timedelta(days=1)
        c.catalog_repo.list_projects.return_value = [c.row, newer]
    else:
        c.row.row_version = True
    with pytest.raises(ConstructionProjectCatalogReadError):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_repository_cannot_return_a_project_outside_authorized_scope(catalog_case):
    c = catalog_case
    project_scope(c, uuid4())
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await read(c)
    no_writes(c)


@pytest.mark.asyncio
async def test_descending_ties_and_actor_bound_continuation(catalog_case):
    c = catalog_case
    rows = []
    for number in (30, 20, 10):
        row = deepcopy(c.row)
        row.id = UUID(int=number)
        rows.append(row)
    c.catalog_repo.list_projects.return_value = rows
    first = await read(c, limit=2)
    assert [item["id"] for item in first.items] == [str(row.id) for row in rows[:2]]
    assert first.next_cursor.project_id == rows[1].id
    c.catalog_repo.list_projects.return_value = rows[2:]
    second = await read(c, limit=2, after=first.next_cursor)
    assert second.items[0]["id"] == str(rows[2].id) and second.next_cursor is None
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["actor", "tenant", "timestamp"])
async def test_foreign_or_tampered_cursor_never_queries_projects(catalog_case, failure):
    c = catalog_case
    cursor = ConstructionProjectCatalogReadCursor(
        uuid4() if failure == "tenant" else c.tenant,
        uuid4() if failure == "actor" else c.user,
        c.now, c.row.id,
    )
    if failure == "timestamp":
        object.__setattr__(cursor, "updated_at", c.now.replace(tzinfo=None))
    with pytest.raises(ConstructionProjectValidationError):
        await read(c, after=cursor)
    c.catalog_repo.list_projects.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, 101, "20"])
async def test_invalid_limit_has_no_query(catalog_case, limit):
    c = catalog_case
    with pytest.raises(ConstructionProjectValidationError):
        await read(c, limit=limit)
    c.catalog_repo.list_projects.assert_not_awaited()
    no_writes(c)


@pytest.mark.asyncio
async def test_real_sql_includes_unlinked_projects_and_paginates_without_gaps(real_case):
    c = real_case
    repository = ConstructionProjectCatalogRepository(c.session)
    identifiers = [UUID(int=value) for value in range(1, 26)]
    statuses = ("draft", "planning", "active", "completed", "cancelled")
    with c.engine.begin() as connection:
        for index, identifier in enumerate(identifiers):
            connection.execute(insert(TABLE).values(**project(
                c, identifier, client_id=None if index % 2 else c.client,
                status=statuses[index % len(statuses)],
            )))
        connection.execute(insert(TABLE).values(**project(
            c, UUID(int=100), tenant_id=c.foreign, client_id=c.foreign_client,
        )))
        connection.execute(insert(TABLE).values(**project(
            c, UUID(int=101), deleted_at=c.now,
        )))
    received, cursor = [], None
    while True:
        rows = await repository.list_projects(
            tenant_id=c.tenant, allowed_project_ids=None, limit=10, after=cursor,
        )
        received.extend(row.id for row in rows[:10])
        if len(rows) <= 10:
            break
        cursor = ConstructionProjectCatalogCursor(c.tenant, c.now, rows[9].id)
    assert received == list(reversed(identifiers))
    assert c.flushes == []
    no_writes(c)


@pytest.mark.asyncio
async def test_real_sql_restricts_scope_before_limit_and_lookahead(real_case):
    c = real_case
    repository = ConstructionProjectCatalogRepository(c.session)
    identifiers = [UUID(int=value) for value in range(1, 7)]
    with c.engine.begin() as connection:
        for identifier in identifiers:
            connection.execute(insert(TABLE).values(**project(c, identifier, client_id=None)))
    rows = await repository.list_projects(
        tenant_id=c.tenant, allowed_project_ids=(identifiers[0], identifiers[3]), limit=1,
    )
    assert [row.id for row in rows] == [identifiers[3], identifiers[0]]
    assert c.flushes == []
    no_writes(c)
