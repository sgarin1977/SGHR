"""Tenant-scoped confirmation storage; not an authorization/consent entry point.

The caller authenticates the target through Platform, validates the hash of the
presented secret, checks fresh edit authority, locks the client, then consumes
this request before its conditional client update. Request/client/audit commit
together. No raw token, identity, membership, grant, permission or public route
is created here. A failed condition returns None; the caller owns rollback.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import exists, insert, literal, select, update

from database.models import ConstructionClient, ConstructionClientUserLinkRequest

MAX_VERSION = 2**63 - 1


def _uuid(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, UUID):
        raise ValueError('Confirmation context requires UUID identifiers.')
    return value


def _version(value):
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise ValueError('Confirmation version must be a positive BIGINT.')
    return value


def _hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Confirmation requires a complete lowercase cryptographic hash.')
    return value


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Confirmation requires aware server timestamps.')
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ConstructionClientLinkRequestState:
    request_id: UUID
    tenant_id: UUID
    client_id: UUID
    initiated_by_user_id: UUID
    target_user_id: UUID
    expected_linked_user_id: UUID | None
    expected_client_row_version: int
    token_hash: str = field(repr=False)
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    updated_at: datetime
    row_version: int
    invalidated_at: datetime | None = None

    def __post_init__(self):
        for name in ('request_id', 'tenant_id', 'client_id', 'initiated_by_user_id', 'target_user_id'):
            _uuid(getattr(self, name))
        _uuid(self.expected_linked_user_id, optional=True)
        _version(self.expected_client_row_version)
        _version(self.row_version)
        _hash(self.token_hash)
        for name in ('issued_at', 'expires_at', 'updated_at'):
            object.__setattr__(self, name, _utc(getattr(self, name)))
        if self.consumed_at is not None:
            object.__setattr__(self, 'consumed_at', _utc(self.consumed_at))
        if self.invalidated_at is not None:
            object.__setattr__(self, 'invalidated_at', _utc(self.invalidated_at))
        if (self.expires_at <= self.issued_at or self.updated_at < self.issued_at
                or (self.consumed_at is not None and not self.issued_at <= self.consumed_at < self.expires_at)
                or (self.invalidated_at is not None and not self.issued_at <= self.invalidated_at < self.expires_at)
                or (self.consumed_at is not None and self.invalidated_at is not None)):
            raise ValueError('Confirmation state timestamps are invalid.')


class ConstructionClientLinkRequestRepository:
    def __init__(self, session):
        if (session is None or not callable(getattr(session, 'execute', None))
                or not callable(getattr(session, 'in_transaction', None))
                or not hasattr(session, 'no_autoflush')):
            raise ValueError('Confirmation storage requires a transaction-capable session.')
        self.session = session

    def _transaction(self):
        if self.session.in_transaction() is not True:
            raise ValueError('Confirmation storage requires an existing caller transaction.')

    @staticmethod
    def _columns():
        c = ConstructionClientUserLinkRequest.__table__.c
        return (c.id.label('request_id'), *[col for col in c if col.name != 'id'])

    @staticmethod
    def _state(row):
        if row is None:
            return None
        if not isinstance(row, Mapping) or set(row) != set(ConstructionClientLinkRequestState.__dataclass_fields__):
            raise ValueError('Confirmation storage receipt fields are invalid.')
        return ConstructionClientLinkRequestState(**row)

    @staticmethod
    def _client_base(*, tenant_id, client_id, version, previous_user_id):
        c = ConstructionClient.__table__.c
        return (c.tenant_id == tenant_id, c.id == client_id, c.deleted_at.is_(None), c.row_version == version,
            c.linked_user_id.is_(None) if previous_user_id is None else c.linked_user_id == previous_user_id)

    async def create_request(self, *, request_id, tenant_id, client_id, initiated_by_user_id,
            target_user_id, expected_linked_user_id, expected_client_row_version, token_hash, issued_at, expires_at):
        state = ConstructionClientLinkRequestState(request_id, tenant_id, client_id, initiated_by_user_id,
            target_user_id, expected_linked_user_id, expected_client_row_version, token_hash,
            issued_at, expires_at, None, issued_at, 1)
        self._transaction()
        table = ConstructionClientUserLinkRequest.__table__
        values = {'id': state.request_id, **{name:getattr(state, name)
            for name in state.__dataclass_fields__ if name != 'request_id'}}
        # Defence in depth: even initiation cannot persist a request for an
        # archived client, wrong tenant or a different base version/identity.
        source = select(*[literal(values[col.name], type_=col.type) for col in table.c]).select_from(
            ConstructionClient.__table__).where(*self._client_base(tenant_id=state.tenant_id,
                client_id=state.client_id, version=state.expected_client_row_version,
                previous_user_id=state.expected_linked_user_id))
        statement = insert(table).from_select([col.name for col in table.c], source,
            include_defaults=False).returning(*self._columns()).execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        self._transaction()
        saved = self._state(result.mappings().one_or_none())
        if saved is not None and saved != state:
            raise ValueError('Created confirmation context differs from the requested base.')
        return saved

    async def get_request_for_update(self, *, tenant_id, request_id, target_user_id, token_hash):
        tenant_id, request_id, target_user_id = _uuid(tenant_id), _uuid(request_id), _uuid(target_user_id)
        token_hash = _hash(token_hash)
        self._transaction()
        c = ConstructionClientUserLinkRequest.__table__.c
        statement = select(*self._columns()).where(c.tenant_id == tenant_id, c.id == request_id,
            c.target_user_id == target_user_id, c.token_hash == token_hash,
        ).with_for_update().execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        self._transaction()
        state = self._state(result.mappings().one_or_none())
        if state is not None and (state.tenant_id != tenant_id or state.request_id != request_id
                or state.target_user_id != target_user_id or state.token_hash != token_hash):
            raise ValueError('Confirmation read context differs.')
        # Return all states so the authorized workflow can distinguish a fresh
        # consume from its prior idempotency result. This grants no consent.
        return state

    async def consume_request(self, *, state, as_of):
        if not isinstance(state, ConstructionClientLinkRequestState):
            raise ValueError('Confirmation consumption requires a typed stored context.')
        state = ConstructionClientLinkRequestState(**{name:getattr(state, name)
            for name in state.__dataclass_fields__})
        as_of = _utc(as_of)
        self._transaction()
        if state.consumed_at is not None or state.invalidated_at is not None or not state.issued_at <= as_of < state.expires_at:
            return None
        if state.row_version == MAX_VERSION:
            raise ValueError('Confirmation version is exhausted.')
        table = ConstructionClientUserLinkRequest.__table__
        c = table.c
        statement = update(table).where(
            c.id == state.request_id, c.tenant_id == state.tenant_id, c.client_id == state.client_id,
            c.initiated_by_user_id == state.initiated_by_user_id, c.target_user_id == state.target_user_id,
            c.expected_client_row_version == state.expected_client_row_version,
            c.expected_linked_user_id.is_(None) if state.expected_linked_user_id is None
                else c.expected_linked_user_id == state.expected_linked_user_id,
            c.token_hash == state.token_hash, c.issued_at == state.issued_at, c.expires_at == state.expires_at,
            c.updated_at == state.updated_at, c.row_version == state.row_version, c.consumed_at.is_(None), c.invalidated_at.is_(None),
            c.issued_at <= as_of, c.expires_at > as_of,
            exists(select(ConstructionClient.id).where(*self._client_base(tenant_id=state.tenant_id,
                client_id=state.client_id, version=state.expected_client_row_version,
                previous_user_id=state.expected_linked_user_id))),
        ).values(consumed_at=as_of, updated_at=as_of, row_version=c.row_version+1).returning(
            *self._columns()).execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        self._transaction()
        saved = self._state(result.mappings().one_or_none())
        expected = ConstructionClientLinkRequestState(**{
            **{name:getattr(state, name) for name in state.__dataclass_fields__},
            'consumed_at':as_of, 'updated_at':as_of, 'row_version':state.row_version+1,
        })
        if saved is not None and saved != expected:
            raise ValueError('Consumed confirmation context differs.')
        return saved


    async def list_active_requests_for_update(self, *, tenant_id, client_id, target_user_id, as_of):
        """Caller locks/authorizes the live client first; no cutoff hides older tokens."""
        tenant_id, client_id, target_user_id = _uuid(tenant_id), _uuid(client_id), _uuid(target_user_id)
        as_of = _utc(as_of)
        self._transaction()
        c = ConstructionClientUserLinkRequest.__table__.c
        statement = select(*self._columns()).where(
            c.tenant_id == tenant_id, c.client_id == client_id, c.target_user_id == target_user_id,
            c.consumed_at.is_(None), c.invalidated_at.is_(None), c.issued_at <= as_of, c.expires_at > as_of,
            exists(select(ConstructionClient.id).where(ConstructionClient.tenant_id == tenant_id,
                ConstructionClient.id == client_id, ConstructionClient.deleted_at.is_(None))),
        ).order_by(c.issued_at, c.id).with_for_update().execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        self._transaction()
        states = tuple(self._state(row) for row in result.mappings().all())
        if any(state is None or state.tenant_id != tenant_id or state.client_id != client_id
                or state.target_user_id != target_user_id or state.consumed_at is not None
                or state.invalidated_at is not None or not state.issued_at <= as_of < state.expires_at
                for state in states):
            raise ValueError('Active confirmation list receipt differs from its requested scope.')
        if len({state.request_id for state in states}) != len(states):
            raise ValueError('Active confirmation list contains duplicate requests.')
        return states

    async def invalidate_request(self, *, state, as_of):
        """Conditionally invalidate one live token; caller composes issuance/audit/commit.

        A stale client base must not preserve an otherwise live old token.
        All original request context and the observed request version still
        participate in the write predicate. Lock order is client then request.
        """
        if not isinstance(state, ConstructionClientLinkRequestState):
            raise ValueError('Invalidation requires a typed stored confirmation context.')
        state = ConstructionClientLinkRequestState(**{name:getattr(state, name)
            for name in state.__dataclass_fields__})
        as_of = _utc(as_of)
        self._transaction()
        if state.consumed_at is not None or state.invalidated_at is not None or not state.issued_at <= as_of < state.expires_at:
            return None
        if state.row_version == MAX_VERSION:
            raise ValueError('Confirmation version is exhausted.')
        table = ConstructionClientUserLinkRequest.__table__
        c = table.c
        statement = update(table).where(
            c.id == state.request_id, c.tenant_id == state.tenant_id, c.client_id == state.client_id,
            c.initiated_by_user_id == state.initiated_by_user_id, c.target_user_id == state.target_user_id,
            c.expected_client_row_version == state.expected_client_row_version,
            c.expected_linked_user_id.is_(None) if state.expected_linked_user_id is None
                else c.expected_linked_user_id == state.expected_linked_user_id,
            c.token_hash == state.token_hash, c.issued_at == state.issued_at, c.expires_at == state.expires_at,
            c.updated_at == state.updated_at, c.row_version == state.row_version,
            c.consumed_at.is_(None), c.invalidated_at.is_(None), c.issued_at <= as_of, c.expires_at > as_of,
            exists(select(ConstructionClient.id).where(ConstructionClient.tenant_id == state.tenant_id,
                ConstructionClient.id == state.client_id, ConstructionClient.deleted_at.is_(None))),
        ).values(invalidated_at=as_of, updated_at=as_of, row_version=c.row_version+1).returning(
            *self._columns()).execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        self._transaction()
        saved = self._state(result.mappings().one_or_none())
        expected = ConstructionClientLinkRequestState(**{
            **{name:getattr(state, name) for name in state.__dataclass_fields__},
            'invalidated_at':as_of, 'updated_at':as_of, 'row_version':state.row_version+1,
        })
        if saved is not None and saved != expected:
            raise ValueError('Invalidated confirmation context differs from the conditional write.')
        return saved
