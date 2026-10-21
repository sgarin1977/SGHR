"""Canonical Platform organization permission rows; caller owns authorization/audit/commit.

This Core table is a query projection, not ORM-managed DDL. The matching guarded
SQL migration defines FKs, checks, partial uniqueness, ACL and RLS. All writers
must use the existing tenant ownership/member barriers before domain row locks.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Column, DateTime, MetaData, Table, Text, Uuid, insert, select, update


MEMBERSHIP_PERMISSION = 'platform.organization.memberships.manage'
organization_permission_grants = Table('platform_organization_permission_grants', MetaData(),
    Column('id', Uuid, primary_key=True),
    Column('tenant_id', Uuid, nullable=False),
    Column('user_id', Uuid, nullable=False),
    Column('permission_code', Text, nullable=False),
    Column('status', Text, nullable=False),
    Column('row_version', BigInteger, nullable=False),
    Column('granted_by_user_id', Uuid, nullable=False),
    Column('granted_at', DateTime(timezone=True), nullable=False),
    Column('revoked_by_user_id', Uuid),
    Column('revoked_at', DateTime(timezone=True)), schema='public')


@dataclass(frozen=True)
class PlatformOrganizationPermissionGrant:
    id: UUID
    tenant_id: UUID
    user_id: UUID
    permission_code: str
    status: str
    row_version: int
    granted_by_user_id: UUID
    granted_at: datetime
    revoked_by_user_id: UUID | None
    revoked_at: datetime | None


def validate_permission_grant(row):
    if (not isinstance(row, PlatformOrganizationPermissionGrant)
            or any(not isinstance(value, UUID) for value in (
                row.id, row.tenant_id, row.user_id, row.granted_by_user_id))
            or row.permission_code != MEMBERSHIP_PERMISSION
            or type(row.row_version) is not int or row.row_version < 1
            or not isinstance(row.granted_at, datetime) or row.granted_at.tzinfo is None
            or row.granted_at.utcoffset() is None
            or row.status not in ('active', 'revoked')):
        raise ValueError('Platform organization permission receipt is invalid.')
    if row.status == 'active':
        if row.revoked_at is not None or row.revoked_by_user_id is not None:
            raise ValueError('Active Platform permission has revocation metadata.')
    elif (not isinstance(row.revoked_by_user_id, UUID) or not isinstance(row.revoked_at, datetime)
            or row.revoked_at.tzinfo is None or row.revoked_at.utcoffset() is None
            or row.revoked_at < row.granted_at or row.row_version < 2):
        raise ValueError('Revoked Platform permission receipt is invalid.')
    return row


class PlatformOrganizationPermissionRepository:
    def __init__(self, session):
        self.session = session

    def _context(self, tenant_id, *ids):
        if any(not isinstance(value, UUID) for value in (tenant_id, *ids)):
            raise ValueError('Platform permission context requires canonical UUIDs.')
        if self.session.in_transaction() is not True:
            raise ValueError('Platform permissions require a caller transaction.')

    @staticmethod
    def _instant(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Platform permission time must be timezone-aware.')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _row(values):
        return validate_permission_grant(PlatformOrganizationPermissionGrant(**dict(values)))

    async def list_for_update(self, *, tenant_id, user_ids):
        if not isinstance(user_ids, tuple) or not user_ids or len(set(user_ids)) != len(user_ids):
            raise ValueError('Permission lookup requires distinct canonical users.')
        self._context(tenant_id, *user_ids)
        t = organization_permission_grants
        with self.session.no_autoflush:
            result = await self.session.execute(select(t).where(
                t.c.tenant_id == tenant_id, t.c.user_id.in_(user_ids),
                t.c.permission_code == MEMBERSHIP_PERMISSION).order_by(t.c.id).with_for_update())
        return tuple(self._row(values) for values in result.mappings().all())

    async def create_grant(self, *, tenant_id, user_id, granted_by_user_id, as_of):
        """Storage only. No authority is derived from this method or caller JWT.

        The trusted Platform caller must authorize explicit issuance, take the
        shared barriers, validate current membership/ADMIN eligibility and write
        its mandatory audit in this same transaction. No public issuer is wired.
        A unique active-row conflict is deliberately not swallowed or committed.
        """
        self._context(tenant_id, user_id, granted_by_user_id)
        instant = self._instant(as_of)
        t = organization_permission_grants
        with self.session.no_autoflush:
            result = await self.session.execute(insert(t).values(id=uuid4(), tenant_id=tenant_id,
                user_id=user_id, permission_code=MEMBERSHIP_PERMISSION, status='active', row_version=1,
                granted_by_user_id=granted_by_user_id, granted_at=instant).returning(*t.c))
        return self._row(result.mappings().one())

    async def revoke_user_grants(self, *, tenant_id, user_id, revoked_by_user_id, as_of):
        """Used by atomic organization removal under the shared ownership barrier."""
        self._context(tenant_id, user_id, revoked_by_user_id)
        instant = self._instant(as_of)
        t = organization_permission_grants
        with self.session.no_autoflush:
            result = await self.session.execute(update(t).where(
                t.c.tenant_id == tenant_id, t.c.user_id == user_id,
                t.c.permission_code == MEMBERSHIP_PERMISSION, t.c.status == 'active',
                t.c.revoked_at.is_(None), t.c.revoked_by_user_id.is_(None))
                .values(status='revoked', row_version=t.c.row_version + 1,
                    revoked_by_user_id=revoked_by_user_id, revoked_at=instant).returning(*t.c))
        return tuple(sorted((self._row(values) for values in result.mappings().all()), key=lambda row: row.id.int))
