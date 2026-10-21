"""Read-only RLS context for the approved own-link tenant lookup.

Only the authenticated Platform user UUID and requested client UUID enter this
transaction. It must finish before ordinary tenant-scoped unlink starts. No
role switch, owner connection, membership or browser-supplied tenant is used.
"""
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text

from database.construction_session import (
    ConstructionDatabaseConfigurationError, _construction_database_identity_guard,
    get_construction_session_factory,
)

OWN_LINK_USER_SETTING = 'app.client_unlink_lookup_user_id'
OWN_LINK_CLIENT_SETTING = 'app.client_unlink_lookup_client_id'


def _identifiers(user_id, client_id):
    if not isinstance(user_id, UUID) or not isinstance(client_id, UUID):
        raise TypeError('Own-link lookup requires verified user and client UUIDs.')


async def bind_client_unlink_lookup_context(session, *, user_id, client_id):
    """Transaction-local ownership hints; RLS and the repository still enforce them."""
    _identifiers(user_id, client_id)
    if session.in_transaction() is not True:
        raise ConstructionDatabaseConfigurationError('Own-link lookup requires an existing read-only transaction.')
    await session.execute(text("""
        SELECT set_config('app.current_tenant_id', '', true),
               set_config('app.current_invitation_id', '', true),
               set_config('app.current_invitation_recipient_id', '', true),
               set_config('app.client_unlink_lookup_user_id', :user_id, true),
               set_config('app.client_unlink_lookup_client_id', :client_id, true),
               set_config('row_security', 'on', true)
    """), {'user_id':str(user_id), 'client_id':str(client_id)})


async def verify_client_unlink_lookup_runtime(session):
    """Fail closed if the actual lookup connection could bypass client RLS."""
    result = await session.execute(text("""
        SELECT current_user AS role_name,
               current_setting('transaction_read_only') = 'on' AS read_only,
               r.rolsuper AS is_superuser, r.rolbypassrls AS bypasses_rls,
               c.relrowsecurity AS rls_enabled,
               pg_catalog.row_security_active(c.oid) AS rls_active,
               pg_catalog.pg_has_role(current_user, c.relowner, 'MEMBER') AS owner_member,
               pg_catalog.to_regclass('construction_clients') = c.oid AS resolves_to_public,
               pg_catalog.has_column_privilege(current_user, c.oid, 'id', 'SELECT')
                 AND pg_catalog.has_column_privilege(current_user, c.oid, 'tenant_id', 'SELECT')
                 AND pg_catalog.has_column_privilege(current_user, c.oid, 'linked_user_id', 'SELECT')
                 AS can_read_projection
        FROM pg_catalog.pg_roles r
        JOIN pg_catalog.pg_class c ON c.oid = pg_catalog.to_regclass('public.construction_clients')
        WHERE r.rolname = current_user AND c.relkind = 'r'
    """))
    row = result.mappings().one_or_none()
    expected={'role_name','read_only','is_superuser','bypasses_rls','rls_enabled','rls_active',
        'owner_member','resolves_to_public','can_read_projection'}
    if (row is None or set(row)!=expected or not isinstance(row['role_name'],str) or not row['role_name']
            or any(row[name] is not True for name in ('read_only','rls_enabled','rls_active','resolves_to_public','can_read_projection'))
            or any(row[name] is not False for name in ('is_superuser','bypasses_rls','owner_member'))):
        raise ConstructionDatabaseConfigurationError('Own-link lookup must use the read-only Construction connection under active client RLS.')


@asynccontextmanager
async def construction_client_unlink_lookup_transaction(*, user_id: UUID, client_id: UUID,
    session_factory=None, identity_guard=None):
    _identifiers(user_id, client_id)
    factory = session_factory if session_factory is not None else get_construction_session_factory()
    guard = identity_guard if identity_guard is not None else _construction_database_identity_guard
    async with factory() as session:
        async with session.begin():
            # This is the first statement of a dedicated transaction. The
            # mutation workflow must use a separate ordinary transaction.
            await session.execute(text('SET TRANSACTION READ ONLY'))
            await guard.ensure_verified(session)
            await bind_client_unlink_lookup_context(session, user_id=user_id, client_id=client_id)
            await verify_client_unlink_lookup_runtime(session)
            yield session
