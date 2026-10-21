"""Internal Platform membership removal, composed by an authorized transaction owner.

The server supplies a shared-session authorizer. Its implementation must check
the existing Platform policy and lock the authority it relies on. This module
introduces no permission or Construction-specific authorization rule. It is not
a public API: Construction grant revocation, its audit and the single commit
must be composed in the same caller transaction before exposing removal.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.api_identity import ApiActorContext


class PlatformMembershipRemovalError(Exception):
    pass


class PlatformMembershipRemovalDeniedError(PlatformMembershipRemovalError):
    pass


@dataclass(frozen=True)
class PlatformMembershipRemovalAuthority:
    """Internal receipt from the configured server authorizer; never a request DTO.

    Locks acquired by that authorizer remain held through the caller's commit.
    valid_until is the earliest expiry of the authority used for this operation.
    """
    actor_user_id: UUID
    tenant_id: UUID
    target_user_id: UUID
    valid_until: datetime | None = None


@dataclass(frozen=True)
class PlatformMembershipRemovalResult:
    user_id: UUID
    tenant_id: UUID
    revoked_role_ids: tuple[UUID, ...]
    revoked_scope_ids: tuple[UUID, ...]
    primary_tenant_cleared: bool
    active_role_cleared: bool
    audit_id: UUID | None
    revoked_permission_grant_ids: tuple[UUID, ...] = ()

    @property
    def changed(self):
        return bool(self.revoked_role_ids or self.revoked_scope_ids or self.revoked_permission_grant_ids
            or self.primary_tenant_cleared or self.active_role_cleared)


class PlatformMembershipService:
    def __init__(self, *, session, repository, event_repository, authorization_service, now_provider=None,
                 permission_repository=None):
        methods = ('get_user_for_update', 'list_tenant_roles_for_update', 'revoke_active_tenant_roles',
            'revoke_active_scopes_of_revoked_tenant_roles', 'clear_primary_tenant_if_matches', 'clear_invalid_active_role')
        if (session is None or not callable(getattr(session, 'in_transaction', None))
                or not callable(getattr(session, 'rollback', None))
                or any(getattr(item, 'session', None) is not session
                    for item in (repository, event_repository, authorization_service))
                or any(not callable(getattr(repository, name, None)) for name in methods)
                or not callable(getattr(event_repository, 'create_event', None))
                or not callable(getattr(authorization_service, 'authorize_removal', None))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Membership removal requires a shared-session server authorizer, repositories and audit.')
        self.session, self.repository, self.events = session, repository, event_repository
        self.authorization = authorization_service
        from database.repositories.platform_organization_permissions import PlatformOrganizationPermissionRepository
        self.permission_repository = (permission_repository if permission_repository is not None
            else PlatformOrganizationPermissionRepository(session))
        if (getattr(self.permission_repository, "session", None) is not session
                or not callable(getattr(self.permission_repository, "revoke_user_grants", None))):
            raise ValueError("Explicit Platform permissions must share the removal transaction.")
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PlatformMembershipRemovalError('Membership server clock must be aware.')
        return value.astimezone(timezone.utc)

    def _authority(self, receipt, actor, target):
        if (not isinstance(receipt, PlatformMembershipRemovalAuthority)
                or receipt.actor_user_id != actor.user_id or receipt.tenant_id != actor.tenant_id
                or receipt.target_user_id != target):
            raise PlatformMembershipRemovalDeniedError('Membership removal authority is unavailable.')
        now = self._now()
        deadline = receipt.valid_until
        if deadline is not None and (not isinstance(deadline, datetime)
                or deadline.tzinfo is None or deadline.utcoffset() is None or deadline <= now):
            raise PlatformMembershipRemovalDeniedError('Membership removal authority has expired.')
        return now

    @staticmethod
    def _user(row, identifier):
        if (row is None or getattr(row, 'id', None) != identifier
                or (row.tenant_id is not None and not isinstance(row.tenant_id, UUID))
                or (row.active_role is not None and not isinstance(row.active_role, str))):
            raise PlatformMembershipRemovalError('Membership target is unavailable.')
        return row

    @staticmethod
    def _roles(rows, target, tenant):
        if not isinstance(rows, (tuple, list)):
            raise PlatformMembershipRemovalError('Membership role receipt is invalid.')
        result = {}
        for row in rows:
            if (not isinstance(getattr(row, 'id', None), UUID) or row.id in result
                    or row.user_id != target or row.tenant_id != tenant
                    or not isinstance(row.role, str) or not row.role
                    or row.status not in ('active', 'suspended', 'revoked')):
                raise PlatformMembershipRemovalError('Membership role receipt is invalid.')
            result[row.id] = (row.role, row.status)
        return result

    async def remove_from_organization(self, *, actor, user_id, trace_id=None):
        """Prepare Platform changes and mandatory audit; caller later revokes grants and commits."""
        try:
            if not self.session.in_transaction():
                raise PlatformMembershipRemovalError('Membership removal requires an existing caller transaction.')
            if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                    or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
                raise PlatformMembershipRemovalDeniedError('Membership actor is unavailable.')
            if not isinstance(user_id, UUID):
                raise ValueError('Membership target must be a UUID.')
            if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128):
                raise ValueError('Membership trace identifier is invalid.')
            with self.session.no_autoflush:
                # Consistent user lock order also covers self-removal. The
                # policy adapter is called after these canonical rows refresh.
                locked = {}
                for identifier in sorted({actor.user_id, user_id}, key=lambda value: value.int):
                    locked[identifier] = await self.repository.get_user_for_update(user_id=identifier)
                current_actor = locked[actor.user_id]
                if current_actor is None or current_actor.id != actor.user_id or current_actor.status != 'active':
                    raise PlatformMembershipRemovalDeniedError('Membership actor is unavailable.')
                authority = await self.authorization.authorize_removal(actor=actor, target_user_id=user_id)
                self._authority(authority, actor, user_id)
                target = self._user(locked[user_id], user_id)
                original_tenant, original_role, original_status = target.tenant_id, target.active_role, target.status
                before = self._roles(await self.repository.list_tenant_roles_for_update(
                    user_id=user_id, tenant_id=actor.tenant_id), user_id, actor.tenant_id)
                self._authority(authority, actor, user_id)
                revoked = self._roles(await self.repository.revoke_active_tenant_roles(
                    user_id=user_id, tenant_id=actor.tenant_id), user_id, actor.tenant_id)
                self._authority(authority, actor, user_id)
                expected = {key for key, (_, status) in before.items() if status == 'active'}
                if (set(revoked) != expected or any(revoked[key] != (before[key][0], 'revoked') for key in revoked)):
                    raise PlatformMembershipRemovalError('Membership role update receipt differs.')
                instant = self._authority(authority, actor, user_id)
                scopes = await self.repository.revoke_active_scopes_of_revoked_tenant_roles(
                    user_id=user_id, tenant_id=actor.tenant_id, revoked_by_user_id=actor.user_id, as_of=instant)
                self._authority(authority, actor, user_id)
                if not isinstance(scopes, (tuple, list)):
                    raise PlatformMembershipRemovalError('Membership scope receipt is invalid.')
                scope_ids = set()
                for row in scopes:
                    parent = before.get(getattr(row, 'user_role_id', None))
                    if (not isinstance(getattr(row, 'id', None), UUID) or row.id in scope_ids
                            or row.user_id != user_id or row.tenant_id != actor.tenant_id
                            or parent is None or parent[0] != row.role or parent[1] not in ('active', 'revoked')
                            or row.status != 'revoked' or row.revoked_by != actor.user_id
                            or row.revoked_at != instant.replace(tzinfo=None)):
                        raise PlatformMembershipRemovalError('Membership scope receipt is invalid.')
                    scope_ids.add(row.id)
                from database.repositories.platform_organization_permissions import validate_permission_grant
                permission_rows = await self.permission_repository.revoke_user_grants(
                    tenant_id=actor.tenant_id, user_id=user_id, revoked_by_user_id=actor.user_id,
                    as_of=self._authority(authority, actor, user_id))
                if not isinstance(permission_rows, tuple):
                    raise PlatformMembershipRemovalError("Explicit Platform permission receipt is invalid.")
                permission_ids = set()
                for row in permission_rows:
                    validate_permission_grant(row)
                    if (row.id in permission_ids or row.tenant_id != actor.tenant_id or row.user_id != user_id
                            or row.status != "revoked" or row.revoked_by_user_id != actor.user_id):
                        raise PlatformMembershipRemovalError("Explicit Platform permission revocation differs.")
                    permission_ids.add(row.id)
                permission_ids = tuple(sorted(permission_ids, key=lambda value: value.int))
                self._authority(authority, actor, user_id)
                cleared = await self.repository.clear_primary_tenant_if_matches(user_id=user_id, tenant_id=actor.tenant_id)
                self._authority(authority, actor, user_id)
                primary_cleared = original_tenant == actor.tenant_id
                if ((cleared is not None) != primary_cleared
                        or (cleared is not None and (cleared.id != user_id or cleared.tenant_id is not None))):
                    raise PlatformMembershipRemovalError('Membership primary tenant receipt differs.')
                changed_selection = await self.repository.clear_invalid_active_role(user_id=user_id)
                self._authority(authority, actor, user_id)
                selection_cleared = changed_selection is not None
                if selection_cleared and (original_role is None or changed_selection.id != user_id
                        or changed_selection.active_role is not None):
                    raise PlatformMembershipRemovalError('Membership active role receipt differs.')
                role_ids = tuple(sorted(revoked, key=lambda value: value.int))
                scope_ids = tuple(sorted(scope_ids, key=lambda value: value.int))
                changed = bool(role_ids or scope_ids or permission_ids or primary_cleared or selection_cleared)
                payload = {'operation': 'platform.membership.remove', 'target_user_id': str(user_id),
                    'revoked_role_ids': [str(value) for value in role_ids],
                    'revoked_scope_ids': [str(value) for value in scope_ids], 'changes': []}
                if permission_ids:
                    payload['revoked_permission_grant_ids'] = [str(value) for value in permission_ids]
                for table, identifiers in (('user_roles', role_ids), ('role_scopes', scope_ids),
                        ('platform_organization_permission_grants', permission_ids)):
                    payload['changes'].extend({'field': f'{table}.{identifier}.status',
                        'old_value': 'active', 'new_value': 'revoked'} for identifier in identifiers)
                if primary_cleared:
                    payload['changes'].append({'field': 'users.tenant_id', 'old_value': str(original_tenant), 'new_value': None})
                if selection_cleared:
                    payload['changes'].append({'field': 'users.active_role', 'old_value': original_role, 'new_value': None})
                audit_id = None
                if changed:
                    audit = await self.events.create_event(event_type='platform_tenant_membership_removed',
                        tenant_id=actor.tenant_id, user_id=actor.user_id, entity_type='platform_membership',
                        entity_id=user_id, payload=deepcopy(payload), platform='api', trace_id=trace_id)
                    self._authority(authority, actor, user_id)
                    if (not isinstance(getattr(audit, 'id', None), UUID)
                            or audit.tenant_id != actor.tenant_id or audit.user_id != actor.user_id
                            or audit.entity_id != user_id or audit.entity_type != 'platform_membership'
                            or audit.event_type != 'platform_tenant_membership_removed' or audit.payload != payload):
                        raise PlatformMembershipRemovalError('Mandatory membership audit receipt differs.')
                    audit_id = audit.id
                final = self._user(await self.repository.get_user_for_update(user_id=user_id), user_id)
                remaining = self._roles(await self.repository.list_tenant_roles_for_update(
                    user_id=user_id, tenant_id=actor.tenant_id), user_id, actor.tenant_id)
                self._authority(authority, actor, user_id)
                if (final.tenant_id != (None if primary_cleared else original_tenant)
                        or final.status != original_status
                        or final.active_role != (None if selection_cleared else original_role)
                        or set(remaining) != set(before)
                        or any(status == 'active' for _, status in remaining.values())
                        or any(remaining[key] != (name, 'revoked' if status == 'active' else status)
                            for key, (name, status) in before.items())):
                    raise PlatformMembershipRemovalError('Membership postcondition failed.')
                return PlatformMembershipRemovalResult(user_id, actor.tenant_id, role_ids, scope_ids,
                    primary_cleared, selection_cleared, audit_id, permission_ids)
        except BaseException:
            await self.session.rollback()
            raise


def build_platform_membership_service(*, session, authorization_service, now_provider=None):
    from database.repositories.event import EventRepository
    from database.repositories.platform_membership import PlatformMembershipRepository
    return PlatformMembershipService(session=session, repository=PlatformMembershipRepository(session),
        event_repository=EventRepository(session), authorization_service=authorization_service, now_provider=now_provider)
