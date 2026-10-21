"""Locked Platform removal authority with approved tenant OWNER default policy."""
from datetime import datetime, timezone
from uuid import UUID

from database.role_policy import ADMINISTRATIVE_ROLES
from database.repositories.platform_membership_authorization import (
    MembershipRemovalEvidence, PlatformMembershipAuthorizationRepository,
)
from services.api_identity import ApiActorContext
from services.construction_membership_removal_policy import require_organization_removal_policy
from services.platform_membership_permissions import (
    PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE, resolve_platform_membership_default_permissions,
    resolve_platform_membership_explicit_permissions,
)
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    require_construction_role_scope,
)
from services.platform_membership import (
    PlatformMembershipRemovalAuthority, PlatformMembershipRemovalDeniedError, PlatformMembershipRemovalError,
)


def _denied():
    raise PlatformMembershipRemovalDeniedError('Platform organization removal authority is unavailable.')


class PlatformMembershipRemovalAuthorizer:
    def __init__(self, *, session, repository, now_provider=None):
        if getattr(repository, 'session', None) is not session:
            raise ValueError('Platform removal authorization requires the same transaction session.')
        self.session, self.repository = session, repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._prepared = None

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PlatformMembershipRemovalError('Platform authorization clock must be timezone-aware.')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _actor(actor, target):
        if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                or any(not isinstance(uid, UUID) for uid in (actor.user_id, actor.tenant_id, target))):
            _denied()

    async def prepare_removal(self, *, actor, target_user_id):
        self._actor(actor, target_user_id)
        self._prepared = None
        evidence = await self.repository.prepare_evidence(tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id, target_user_id=target_user_id)
        if (not isinstance(evidence, MembershipRemovalEvidence)
                or (evidence.tenant_id, evidence.actor_user_id, evidence.target_user_id) !=
                   (actor.tenant_id, actor.user_id, target_user_id)
                or evidence.transaction is not self.repository.transaction()):
            _denied()
        self._prepared = evidence

    async def authorize_removal(self, *, actor, target_user_id):
        self._actor(actor, target_user_id)
        e = self._prepared
        if (not isinstance(e, MembershipRemovalEvidence)
                or e.transaction is not self.repository.transaction()
                or (e.tenant_id,e.actor_user_id,e.target_user_id) !=
                   (actor.tenant_id,actor.user_id,target_user_id)):
            _denied()
        now = self._now()  # Re-evaluate expiry AFTER every preparation wait.
        users = {u.id:u for u in e.users}
        if len(users) != len(e.users) or any(not isinstance(uid, UUID) for uid in users): _denied()
        if actor.user_id not in users or target_user_id not in users or users[actor.user_id].status != 'active': _denied()
        roles = []
        role_ids = set()
        for r in e.roles:
            if (not isinstance(r.id, UUID) or r.id in role_ids or r.tenant_id != actor.tenant_id
                    or r.user_id not in users or not isinstance(r.role,str)
                    or r.status not in ('active','suspended','revoked')): _denied()
            role_ids.add(r.id)
            if r.status == 'active': roles.append(r)
        members = {u.id for u in e.users if u.status == 'active' and (u.tenant_id == actor.tenant_id
            or any(r.user_id == u.id for r in roles))}
        if actor.user_id not in members: _denied()
        for s in e.scopes:
            if (not isinstance(s.id,UUID) or s.tenant_id != actor.tenant_id or s.user_id not in users
                    or s.status not in ('active','suspended','revoked')): _denied()
        def unrestricted(r):
            # Even malformed/orphan scope-parent metadata must never widen a
            # scoped role into organization-wide membership authority.
            return not any(s.status == 'active' and s.user_id == r.user_id and s.role == r.role for s in e.scopes)
        actor_roles = tuple(r for r in roles if r.user_id == actor.user_id and unrestricted(r))
        permissions = {r.role:set() for r in roles}
        for row in e.role_permissions:
            role, code = row['role'], row['permission_code']
            if role not in permissions or type(code) is not str or not code or code != code.strip(): _denied()
            permissions[role].add(code)
        platform_codes = set().union(*(permissions[r.role] for r in actor_roles))
        # A shared role template has no user/tenant assignment context.
        # It cannot provide the newly approved explicit ADMIN authority.
        platform_codes.discard(PLATFORM_ORGANIZATION_MEMBERSHIP_MANAGE)
        current = []
        seen = set()
        for g in e.grants:
            if (not isinstance(g.id,UUID) or g.id in seen or g.tenant_id != actor.tenant_id
                    or not isinstance(g.user_id,UUID) or not isinstance(g.scope_id,UUID)
                    or g.status != 'active' or g.revoked_at is not None or g.revoked_by_user_id is not None
                    or (g.scope_type == 'tenant' and g.scope_id != actor.tenant_id)): _denied()
            seen.add(g.id)
            require_construction_role_scope(role=g.role,scope_type=g.scope_type)
            expiry = g.expires_at
            if expiry is not None and (not isinstance(expiry,datetime) or expiry.tzinfo is None or expiry.utcoffset() is None): _denied()
            if expiry is None or expiry > now: current.append(g)
        def access(uid):
            grants = tuple(ConstructionGrantContext(g.role,g.scope_type,g.scope_id) for g in current if g.user_id == uid)
            names = tuple(sorted({g.role for g in grants}))
            codes = tuple(sorted(set().union(*(CONSTRUCTION_ROLE_PERMISSIONS[r] for r in names))))
            return ConstructionAccessContext(uid, actor.tenant_id, names, codes, grants)
        actor_access, target_access = access(actor.user_id), access(target_user_id)
        owners = frozenset(g.user_id for g in current if g.role == 'OWNER' and g.scope_type == 'tenant'
            and g.scope_id == actor.tenant_id and g.user_id in members)
        # Default grant is a Platform policy result from fresh locked ownership,
        # not a role-name bypass of the mandatory Platform permission gate.
        platform_codes.update(resolve_platform_membership_default_permissions(
            actor=actor, construction_access_context=actor_access))
        from database.repositories.platform_organization_permissions import validate_permission_grant
        if not isinstance(e.permission_grants, tuple): _denied()
        for row in e.permission_grants:
            try: validate_permission_grant(row)
            except ValueError: _denied()
            if row.user_id not in (actor.user_id,target_user_id) or row.granted_at > now: _denied()
        platform_codes.update(resolve_platform_membership_explicit_permissions(
            actor=actor, construction_access_context=actor_access, permission_grants=e.permission_grants))
        require_organization_removal_policy(actor=actor,actor_access=actor_access,target_access=target_access,
            platform_permission_codes=tuple(sorted(platform_codes)),active_tenant_owner_user_ids=owners)
        is_owner = actor.user_id in owners
        # Preserve global roles; only tenant-local active administrative roles
        # are affected. A Platform-policy-authorized tenant OWNER may remove
        # tenant ADMIN membership without receiving the ADMIN's other powers.
        # For a non-owner, actual Platform permission dominance is independent
        # from the Construction matrix. Global super_admin semantics remain a
        # Root boundary even for legacy assignments carrying a tenant_id.
        for target_role in (r for r in roles if r.user_id == target_user_id and r.role in ADMINISTRATIVE_ROLES):
            target_codes = permissions[target_role.role]
            if target_role.role == 'super_admin': _denied()
            if not is_owner and not any(r.role in ADMINISTRATIVE_ROLES and r.role != target_role.role
                    and target_codes < permissions[r.role] for r in actor_roles):
                _denied()
        relevant = [g for g in current if g.user_id in (actor.user_id,target_user_id)
                    or (g.role == 'OWNER' and g.scope_type == 'tenant' and g.user_id in owners)]
        deadline = min((g.expires_at for g in relevant if g.expires_at is not None), default=None)
        return PlatformMembershipRemovalAuthority(actor.user_id,actor.tenant_id,target_user_id,deadline)


def build_authorized_organization_removal_workflow(*, session, now_provider=None):
    from services.construction_organization_removal_workflow import build_construction_organization_removal_workflow
    authorization = PlatformMembershipRemovalAuthorizer(session=session,
        repository=PlatformMembershipAuthorizationRepository(session),now_provider=now_provider)
    return build_construction_organization_removal_workflow(session=session,
        authorization_service=authorization,now_provider=now_provider)
