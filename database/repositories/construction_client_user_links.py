"""Internal client identity-link storage; never an authorization entry point.

The caller must obtain fresh Construction edit authority and the target user's
confirmation before linking/replacing, or approved unlink authority. It owns
mandatory audit and commit in this same transaction. No membership, access grant,
Telegram binding or user identity is created here. A None result is a failed
conditional operation, never permission to retry as an unconditional overwrite.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update

from database.models import ConstructionClient


MAX_CLIENT_ROW_VERSION = 2**63 - 1


@dataclass(frozen=True)
class ConstructionClientUserLinkState:
    client_id: UUID
    tenant_id: UUID
    linked_user_id: UUID | None
    row_version: int


@dataclass(frozen=True)
class ConstructionClientUserLinkMutation:
    state: ConstructionClientUserLinkState
    changed: bool


def _uuid(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, UUID):
        raise ValueError('Client link identifiers must be UUID values.')
    return value


def _version(value):
    if type(value) is not int or not 1 <= value <= MAX_CLIENT_ROW_VERSION:
        raise ValueError('Client link version must be a positive BIGINT.')
    return value


class ConstructionClientUserLinkRepository:
    def __init__(self, session):
        if (session is None or not callable(getattr(session, 'execute', None))
                or not callable(getattr(session, 'in_transaction', None))
                or not hasattr(session, 'no_autoflush')):
            raise ValueError('Client link storage requires a transaction-capable session.')
        self.session = session

    def _transaction(self):
        if self.session.in_transaction() is not True:
            raise ValueError('Client link storage requires an existing caller transaction.')

    @staticmethod
    def _columns():
        c = ConstructionClient.__table__.c
        return (c.id.label('client_id'), c.tenant_id, c.linked_user_id, c.row_version)

    @staticmethod
    def _state(row, *, tenant_id, client_id):
        if row is None:
            return None
        if (not isinstance(row, Mapping)
                or set(row) != {'client_id', 'tenant_id', 'linked_user_id', 'row_version'}
                or row['client_id'] != client_id or row['tenant_id'] != tenant_id):
            raise ValueError('Client link storage receipt has invalid context.')
        return ConstructionClientUserLinkState(
            _uuid(row['client_id']), _uuid(row['tenant_id']),
            _uuid(row['linked_user_id'], optional=True), _version(row['row_version']),
        )

    async def get_state_for_update(self, *, tenant_id, client_id):
        """Lock a live client and read only the identity/version projection."""
        _uuid(tenant_id)
        _uuid(client_id)
        self._transaction()
        c = ConstructionClient.__table__.c
        statement = select(*self._columns()).where(
            c.tenant_id == tenant_id, c.id == client_id, c.deleted_at.is_(None),
        ).with_for_update().execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        self._transaction()
        return self._state(result.mappings().one_or_none(), tenant_id=tenant_id, client_id=client_id)

    async def change_linked_user(self, *, tenant_id, client_id, expected_row_version,
            expected_linked_user_id, linked_user_id, as_of):
        """Apply an authorized and confirmed transition, conditional on its exact base.

        No-op still requires the current version and previous identity to match.
        Changed state and mandatory audit must be committed by the caller together.
        FK/database failures propagate to that caller; this adapter never commits
        or rolls back only part of a composed business transaction.
        """
        _uuid(tenant_id)
        _uuid(client_id)
        _uuid(expected_linked_user_id, optional=True)
        _uuid(linked_user_id, optional=True)
        _version(expected_row_version)
        if (not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError('Client link timestamp must be timezone-aware.')
        as_of = as_of.astimezone(timezone.utc)
        self._transaction()
        if expected_linked_user_id == linked_user_id:
            state = await self.get_state_for_update(tenant_id=tenant_id, client_id=client_id)
            if (state is None or state.row_version != expected_row_version
                    or state.linked_user_id != expected_linked_user_id):
                return None
            return ConstructionClientUserLinkMutation(state, False)
        if expected_row_version == MAX_CLIENT_ROW_VERSION:
            raise ValueError('Client link version is exhausted.')
        table = ConstructionClient.__table__
        c = table.c
        statement = update(table).where(
            c.tenant_id == tenant_id, c.id == client_id, c.deleted_at.is_(None),
            c.row_version == expected_row_version,
            c.linked_user_id.is_(None) if expected_linked_user_id is None
                else c.linked_user_id == expected_linked_user_id,
        ).values(linked_user_id=linked_user_id, row_version=c.row_version + 1,
            updated_at=as_of).returning(*self._columns()).execution_options(autoflush=False)
        with self.session.no_autoflush:
            result = await self.session.execute(statement)
        self._transaction()
        state = self._state(result.mappings().one_or_none(), tenant_id=tenant_id, client_id=client_id)
        if state is None:
            return None
        if state.row_version != expected_row_version + 1 or state.linked_user_id != linked_user_id:
            raise ValueError('Client link conditional write receipt differs.')
        return ConstructionClientUserLinkMutation(state, True)
