from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from database.repositories.construction import ConstructionOrganizationRepository
from services.api_identity import ApiActorContext
from services.construction_foundation import (
    ConstructionOrganizationService, ConstructionOrganizationUnavailableError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['active', 'paused', 'suspended', 'deleted'])
async def test_organization_context_uses_restricted_columns_and_active_current_tenant(status):
    """Exercise ORM materialization; SQLite validates projection, not PostgreSQL RLS."""
    engine = create_engine('sqlite://')
    requested, foreign = uuid4(), uuid4()
    queries = []

    @event.listens_for(engine, 'before_cursor_execute')
    def capture(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)

    try:
        with engine.begin() as connection:
            connection.execute(text('''CREATE TABLE tenants (
                id CHAR(32) PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL,
                default_language VARCHAR(10) NOT NULL, default_currency VARCHAR(3) NOT NULL,
                status TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )'''))
            for identity, state in [(requested, status), (foreign, 'active')]:
                connection.execute(text('''INSERT INTO tenants VALUES (
                    :id, 'Company', :slug, 'ru', 'EUR', :status, '{}',
                    '2026-10-13 00:00:00', '2026-10-13 00:00:00')'''),
                    {'id': identity.hex, 'slug': identity.hex, 'status': state})
        with Session(engine) as session:
            async def execute(statement):
                return session.execute(statement)
            repository = ConstructionOrganizationRepository(SimpleNamespace(execute=execute))
            actor = ApiActorContext(user_id=uuid4(), tenant_id=requested,
                active_role='client', roles=('client',), language_code='ru', timezone=None, status='active')
            service = ConstructionOrganizationService(repository=repository)
            if status == 'active':
                context = await service.require_current(actor=actor)
                assert context.tenant_id == requested
                assert (context.name, context.slug, context.default_language, context.default_currency) == (
                    'Company', requested.hex, 'ru', 'EUR')
                tenant = await repository.get_active_organization(tenant_id=requested)
                assert {'extra_metadata', 'created_at', 'updated_at'} <= inspect(tenant).unloaded
                count = len(queries)
                for attribute in ('extra_metadata', 'created_at', 'updated_at'):
                    with pytest.raises(InvalidRequestError):
                        getattr(tenant, attribute)
                assert len(queries) == count, 'Deferred access must not issue additional tenant SELECTs.'
            else:
                with pytest.raises(ConstructionOrganizationUnavailableError):
                    await service.require_current(actor=actor)
            assert await repository.get_active_organization(tenant_id=uuid4()) is None
            assert queries
            for query in queries:
                projection = query.split('FROM')[0]
                assert 'metadata' not in projection and 'created_at' not in projection and 'updated_at' not in projection
    finally:
        engine.dispose()
