"""Regression for PostgreSQL 42601 caused by subscripting an ungrouped CASE."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID
import re

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import asyncpg

from database.repositories.construction.construction_client_history import (
    ConstructionClientHistoryRepository, ConstructionClientHistoryCursor, CLIENT_HISTORY_RECORD_FIELDS,
    CLIENT_HISTORY_ACTIVITY_EVIDENCE_FIELDS,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('cursor_page', [False, True])
@pytest.mark.parametrize('subscript_syntax', [False, True])
@pytest.mark.parametrize('driver', ['postgresql', 'asyncpg'])
async def test_real_history_select_groups_case_before_jsonb_access(driver, subscript_syntax, cursor_page):
    tenant, client = UUID(int=1), UUID(int=2)
    cursor = ConstructionClientHistoryCursor(tenant, client,
        datetime(2026, 10, 14, 12, 0, 0, 123456, tzinfo=timezone.utc), UUID(int=3)) if cursor_page else None
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        mappings=lambda: SimpleNamespace(all=lambda: []))), add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    repository = ConstructionClientHistoryRepository(session)
    await repository.list_history_for_client(tenant_id=tenant, client_id=client, after=cursor)
    statement = session.execute.await_args.args[0]
    dialect = asyncpg.dialect() if driver == 'asyncpg' else postgresql.dialect()
    # Cover both SQLAlchemy's -> rendering and its PostgreSQL JSONB [] rendering.
    dialect._supports_jsonb_subscripting = subscript_syntax
    compiled = statement.compile(dialect=dialect)
    sql = str(compiled)
    assert 'CASE WHEN' in sql
    assert not re.search(r'\bEND\s*\[', sql), 'PostgreSQL rejects CASE ... END[key] without parentheses.'
    if subscript_syntax:
        assert re.search(r'\bEND\)\[', sql), 'JSONB subscripting must apply to the whole grouped CASE.'
    else:
        assert ' -> ' in sql and 'END)' in sql
    assert [c.name for c in statement.selected_columns] == [*CLIENT_HISTORY_RECORD_FIELDS, *CLIENT_HISTORY_ACTIVITY_EVIDENCE_FIELDS]
    assert "construction_activities.lead_id IS NULL" in sql and "construction_activities.project_id IS NULL" in sql
    assert tenant in compiled.params.values() and str(client) in compiled.params.values()
    assert 'ORDER BY event_logs.created_at DESC, event_logs.id DESC' in sql
    assert 'FOR UPDATE' not in sql
    if cursor_page:
        assert 'EXTRACT(epoch FROM event_logs.created_at)' in sql
        assert cursor.epoch in compiled.params.values()
    for method in ('add', 'flush', 'commit', 'rollback'):
        getattr(session, method).assert_not_called()
