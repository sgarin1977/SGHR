"""Locked existing Platform/Construction evidence. No writes or new ACL grants."""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import bindparam, select, text
from database.models import ConstructionAccessGrant, RoleScope, User, UserRoleMapping
from database.repositories.platform_membership_serialization import ownership_lock_key
from services.platform_membership import PlatformMembershipRemovalDeniedError, PlatformMembershipRemovalError


@dataclass(frozen=True)
class MembershipRemovalEvidence:
    tenant_id: UUID
    actor_user_id: UUID
    target_user_id: UUID
    transaction: object
    users: tuple
    roles: tuple
    scopes: tuple
    grants: tuple
    role_permissions: tuple
    permission_grants: tuple = ()


class PlatformMembershipAuthorizationRepository:
    def __init__(self, session):
        self.session = session

    def transaction(self):
        session = self.session
        transaction = session.get_transaction()
        if (session.in_transaction() is not True or transaction is None or transaction.is_active is not True
                or session.get_nested_transaction() is not None
                or getattr(getattr(session.get_bind(), 'dialect', None), 'name', None) != 'postgresql'):
            raise PlatformMembershipRemovalError('Platform authorization requires an outer PostgreSQL transaction.')
        return transaction

    async def prepare_evidence(self, *, tenant_id, actor_user_id, target_user_id):
        if any(not isinstance(uid, UUID) for uid in (tenant_id, actor_user_id, target_user_id)):
            raise PlatformMembershipRemovalDeniedError('Platform membership identity is invalid.')
        transaction = self.transaction()
        key = ownership_lock_key(tenant_id=tenant_id) & ((1 << 64) - 1)
        with self.session.no_autoflush:
            result = await self.session.execute(text("""
                SELECT current_user AS role_name,
                  current_setting('transaction_read_only') AS read_only,
                  current_setting('transaction_isolation') AS isolation,
                  EXISTS (SELECT 1 FROM pg_catalog.pg_locks
                    WHERE locktype='advisory' AND pid=pg_catalog.pg_backend_pid()
                      AND database=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
                      AND classid=CAST(:hi AS oid) AND objid=CAST(:lo AS oid)
                      AND objsubid=1 AND granted) AS ownership_locked
            """), {'hi': key >> 32, 'lo': key & ((1 << 32)-1)})
            context = result.mappings().one()
            if (context['read_only'] != 'off' or context['isolation'] != 'read committed'
                    or context['ownership_locked'] is not True
                    or context['role_name'] in ('anon','authenticated','service_role','construction_api')):
                raise PlatformMembershipRemovalDeniedError('A serialized Platform runtime transaction is required.')
            # Freeze the shared permission catalogue, including absent mappings,
            # before user/role locks. A cached JWT permission is never evidence.
            await self.session.execute(text('LOCK TABLE public.permissions, public.role_permissions IN SHARE MODE'))
            # Freeze explicit assignments, including absent rows. One lock mode
            # for readers/removal writers avoids SHARE-to-write upgrade deadlocks.
            await self.session.execute(text('LOCK TABLE public.platform_organization_permission_grants IN SHARE ROW EXCLUSIVE MODE'))
            grants = tuple((await self.session.execute(select(ConstructionAccessGrant).where(
                ConstructionAccessGrant.tenant_id == tenant_id, ConstructionAccessGrant.status == 'active'
            ).order_by(ConstructionAccessGrant.id).execution_options(populate_existing=True, autoflush=False)
                .with_for_update())).scalars().all())
            owners = {g.user_id for g in grants if g.role == 'OWNER'
                      and g.scope_type == 'tenant' and g.scope_id == tenant_id}
            ids = tuple(sorted(owners | {actor_user_id, target_user_id}, key=lambda uid: uid.int))
            users = tuple((await self.session.execute(select(User).where(User.id.in_(ids)).order_by(User.id)
                .execution_options(populate_existing=True, autoflush=False).with_for_update())).scalars().all())
            roles = tuple((await self.session.execute(select(UserRoleMapping).where(
                UserRoleMapping.tenant_id == tenant_id, UserRoleMapping.user_id.in_(ids)
            ).order_by(UserRoleMapping.id).execution_options(populate_existing=True, autoflush=False)
                .with_for_update())).scalars().all())
            scopes = tuple((await self.session.execute(select(RoleScope).where(
                RoleScope.tenant_id == tenant_id, RoleScope.user_id.in_(ids)
            ).order_by(RoleScope.id).execution_options(populate_existing=True, autoflush=False)
                .with_for_update())).scalars().all())
            names = tuple(sorted({r.role for r in roles if r.status == 'active'}))
            permission_rows = ()
            if names:
                statement = text('SELECT rp.role, rp.permission_code FROM public.role_permissions rp '
                    'JOIN public.permissions p ON p.code=rp.permission_code '
                    'WHERE rp.role IN :roles ORDER BY rp.role,rp.permission_code').bindparams(
                        bindparam('roles', expanding=True))
                permission_rows = tuple(dict(row) for row in (await self.session.execute(
                    statement, {'roles':names})).mappings().all())
            from database.repositories.platform_organization_permissions import PlatformOrganizationPermissionRepository
            permission_grants = await PlatformOrganizationPermissionRepository(self.session).list_for_update(
                tenant_id=tenant_id, user_ids=tuple(sorted({actor_user_id,target_user_id}, key=lambda uid: uid.int)))
            if self.transaction() is not transaction:
                raise PlatformMembershipRemovalError('Platform authorization transaction changed.')
        return MembershipRemovalEvidence(tenant_id, actor_user_id, target_user_id, transaction,
            users, roles, scopes, grants, permission_rows, permission_grants)
