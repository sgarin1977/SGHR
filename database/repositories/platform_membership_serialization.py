"""Shared PostgreSQL transaction lock for one canonical tenant/member pair.

Every participating membership/grant writer must take this lock BEFORE any
user, project, invitation or grant row locks and then refresh its checks.
This primitive is not an authorization decision and does not activate writers.
Transaction completion releases the lock; there is no session-level unlock.
"""
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import UUID

from sqlalchemy import BigInteger, bindparam, text


class PlatformMembershipSerializationError(Exception):
    pass


@dataclass(frozen=True)
class PlatformMembershipSerializationReceipt:
    tenant_id: UUID
    user_id: UUID
    transaction: object = field(repr=False, compare=False)


def membership_lock_key(*, tenant_id, user_id):
    if not isinstance(tenant_id, UUID) or not isinstance(user_id, UUID):
        raise ValueError('Membership serialization requires canonical UUIDs.')
    # Stable across workers/processes; never Python's randomized hash().
    digest = sha256(b'sghr.platform.membership.v1\0' + tenant_id.bytes + user_id.bytes).digest()
    return int.from_bytes(digest[:8], 'big', signed=True)


def ownership_lock_key(*, tenant_id):
    if not isinstance(tenant_id, UUID):
        raise ValueError('Ownership serialization requires a canonical tenant UUID.')
    digest = sha256(b'sghr.platform.ownership.v1\0' + tenant_id.bytes).digest()
    return int.from_bytes(digest[:8], 'big', signed=True)


class PlatformMembershipSerializationRepository:
    def __init__(self, session):
        if (session is None or not hasattr(session, 'no_autoflush') or any(
                not callable(getattr(session, name, None)) for name in (
                    'execute', 'get_bind', 'get_transaction', 'get_nested_transaction', 'in_transaction'))):
            raise ValueError('Membership serialization requires one transaction session.')
        self.session = session

    def _transaction(self):
        transaction = self.session.get_transaction()
        if (self.session.in_transaction() is not True or transaction is None
                or transaction.is_active is not True or self.session.get_nested_transaction() is not None):
            raise PlatformMembershipSerializationError('Membership serialization requires an active outer transaction.')
        return transaction

    async def lock_member(self, *, tenant_id, user_id):
        key = membership_lock_key(tenant_id=tenant_id, user_id=user_id)
        transaction = self._transaction()
        bind = self.session.get_bind()
        if getattr(getattr(bind, 'dialect', None), 'name', None) != 'postgresql':
            raise PlatformMembershipSerializationError('Membership serialization requires PostgreSQL.')
        with self.session.no_autoflush:
            result = await self.session.execute(text(
                "SELECT pg_catalog.current_setting('transaction_read_only') AS read_only, "
                "pg_catalog.current_setting('transaction_isolation') AS isolation"
            ))
            settings = result.mappings().one()
            # READ COMMITTED refreshes the next business query AFTER waiting.
            # A repeatable-read snapshot could retain pre-removal membership.
            if settings['read_only'] != 'off' or settings['isolation'] != 'read committed':
                raise PlatformMembershipSerializationError('Membership serialization requires read-write READ COMMITTED.')
            if self._transaction() is not transaction:
                raise PlatformMembershipSerializationError('Membership transaction changed before serialization.')
            # Every existing cooperating writer takes this BEFORE member/user locks.
            # Coarse tenant serialization also protects distinct self-removals.
            ownership = text('SELECT pg_catalog.pg_advisory_xact_lock(CAST(:ownership_key AS bigint))').bindparams(
                bindparam('ownership_key', value=ownership_lock_key(tenant_id=tenant_id), type_=BigInteger()))
            await self.session.execute(ownership)
            if self._transaction() is not transaction:
                raise PlatformMembershipSerializationError('Ownership transaction changed while serializing.')
            statement = text('SELECT pg_catalog.pg_advisory_xact_lock(CAST(:lock_key AS bigint))').bindparams(
                bindparam('lock_key', value=key, type_=BigInteger()))
            await self.session.execute(statement)
            if self._transaction() is not transaction:
                raise PlatformMembershipSerializationError('Membership transaction changed while serializing.')
        return PlatformMembershipSerializationReceipt(tenant_id, user_id, transaction)
