"""Compose approved Platform membership removal and Construction revocation.

Internal transaction preparation only: caller owns the single final commit,
runtime connection, and serialization with concurrent grant issuance/acceptance.
No public route/default authorization policy is selected here. The required
shared-session Platform authorizer must lock its authority through that commit.
Platform membership remains owned by the existing PlatformMembershipService.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.api_identity import ApiActorContext
from services.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction_permissions import require_construction_role_scope
from services.platform_membership import (
    PlatformMembershipService, PlatformMembershipRemovalAuthority,
    PlatformMembershipRemovalDeniedError, PlatformMembershipRemovalError, PlatformMembershipRemovalResult,
)


@dataclass(frozen=True)
class ConstructionOrganizationRemovalResult:
    membership: PlatformMembershipRemovalResult
    revoked_grant_ids: tuple[UUID, ...]
    grant_audit_ids: tuple[UUID, ...]

    @property
    def changed(self):
        return self.membership.changed or bool(self.revoked_grant_ids)


class _AuthorityCapture:
    def __init__(self, authorization):
        self.session = authorization.session
        self.delegate = authorization
        self.receipt = None

    async def authorize_removal(self, *, actor, target_user_id):
        self.receipt = await self.delegate.authorize_removal(actor=actor, target_user_id=target_user_id)
        return self.receipt


class ConstructionOrganizationRemovalService:
    def __init__(self, *, session, membership_repository, grant_repository, event_repository,
        authorization_service, now_provider=None):
        if (session is None or not hasattr(session, 'no_autoflush')
                or any(not callable(getattr(session, name, None)) for name in ('in_transaction', 'rollback'))
                or any(getattr(item, 'session', None) is not session for item in (
                    membership_repository, grant_repository, event_repository, authorization_service))
                or any(not callable(getattr(grant_repository, name, None)) for name in (
                    'list_active_grants', 'get_grant_for_update', 'revoke_effective_user_grants', 'has_active_platform_membership'))
                or not callable(getattr(authorization_service, 'authorize_removal', None))
                or not callable(getattr(event_repository, 'create_event', None))
                or (now_provider is not None and not callable(now_provider))):
            raise ValueError('Organization removal requires shared-session Platform authorization, membership, grants and audit.')
        self.session, self.membership, self.grants = session, membership_repository, grant_repository
        self.events, self.authorization = event_repository, authorization_service
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.audit = ConstructionAuditLogger(event_repository=event_repository)
        # Validate Core wiring now; a per-call capture below retains only the
        # authority that Core actually authorized after its ordered user locks.
        PlatformMembershipService(session=session, repository=membership_repository, event_repository=event_repository,
            authorization_service=authorization_service, now_provider=self._now)

    def _now(self):
        value = self.now_provider()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PlatformMembershipRemovalError('Organization removal server clock is invalid.')
        return value.astimezone(timezone.utc)

    def _authority(self, capture, actor, target):
        receipt = capture.receipt
        if (not isinstance(receipt, PlatformMembershipRemovalAuthority) or receipt.actor_user_id != actor.user_id
                or receipt.tenant_id != actor.tenant_id or receipt.target_user_id != target):
            raise PlatformMembershipRemovalDeniedError('Organization removal authority is unavailable.')
        instant = self._now()
        expiry = receipt.valid_until
        if expiry is not None and (not isinstance(expiry, datetime) or expiry.tzinfo is None
                or expiry.utcoffset() is None or expiry <= instant):
            raise PlatformMembershipRemovalDeniedError('Organization removal authority has expired.')
        return instant

    @staticmethod
    def _membership_result(value, actor, target):
        if (not isinstance(value, PlatformMembershipRemovalResult) or value.user_id != target or value.tenant_id != actor.tenant_id
                or type(value.primary_tenant_cleared) is not bool or type(value.active_role_cleared) is not bool
                or any(not isinstance(ids, tuple) or any(not isinstance(v, UUID) for v in ids)
                    or ids != tuple(sorted(set(ids), key=lambda v: v.int)) for ids in (value.revoked_role_ids, value.revoked_scope_ids, value.revoked_permission_grant_ids))
                or (value.changed and not isinstance(value.audit_id, UUID)) or (not value.changed and value.audit_id is not None)):
            raise PlatformMembershipRemovalError('Platform membership removal receipt is invalid.')
        return value

    @staticmethod
    def _grant(row, actor, target):
        fields = ('id', 'tenant_id', 'user_id', 'role', 'scope_type', 'scope_id', 'expires_at', 'granted_by_user_id', 'granted_at')
        if (row is None or any(not hasattr(row, field) for field in (*fields, 'status', 'revoked_at', 'revoked_by_user_id'))
                or any(not isinstance(getattr(row, name), UUID) for name in ('id','tenant_id','user_id','scope_id','granted_by_user_id'))
                or row.tenant_id != actor.tenant_id or row.user_id != target
                or (row.scope_type == 'tenant' and row.scope_id != actor.tenant_id)):
            raise PlatformMembershipRemovalError('Construction grant context is invalid.')
        require_construction_role_scope(role=row.role, scope_type=row.scope_type)
        if (not isinstance(row.granted_at, datetime) or row.granted_at.tzinfo is None or row.granted_at.utcoffset() is None
                or (row.expires_at is not None and (not isinstance(row.expires_at, datetime)
                    or row.expires_at.tzinfo is None or row.expires_at.utcoffset() is None or row.expires_at <= row.granted_at))):
            raise PlatformMembershipRemovalError('Construction grant history is invalid.')
        return tuple(getattr(row, field) for field in fields)

    @staticmethod
    def _active(row, instant):
        return (row.status == 'active' and row.revoked_at is None and row.revoked_by_user_id is None
            and (row.expires_at is None or row.expires_at > instant))

    async def remove_from_organization(self, *, actor, user_id, trace_id=None):
        """Prepare all changes; no commit and no public authorization inference."""
        try:
            if (not isinstance(actor, ApiActorContext) or actor.status != 'active'
                    or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
                raise PlatformMembershipRemovalDeniedError('Organization removal actor is unavailable.')
            if not isinstance(user_id, UUID) or (trace_id is not None and (
                    not isinstance(trace_id, str) or not trace_id.strip() or len(trace_id) > 128)):
                raise ValueError('Organization removal request is invalid.')
            if self.session.in_transaction() is not True:
                raise PlatformMembershipRemovalError('Organization removal requires a caller transaction.')
            capture = _AuthorityCapture(self.authorization)
            core = PlatformMembershipService(session=self.session, repository=self.membership, event_repository=self.events,
                authorization_service=capture, now_provider=self._now)
            with self.session.no_autoflush:
                membership = self._membership_result(await core.remove_from_organization(
                    actor=actor, user_id=user_id, trace_id=trace_id), actor, user_id)
                observed_at = self._authority(capture, actor, user_id)
                candidates = await self.grants.list_active_grants(tenant_id=actor.tenant_id, user_id=user_id, as_of=observed_at)
                self._authority(capture, actor, user_id)
                if not isinstance(candidates, (tuple, list)):
                    raise PlatformMembershipRemovalError('Construction grant list is invalid.')
                before = {}
                for row in candidates:
                    history = self._grant(row, actor, user_id)
                    if row.id in before or not self._active(row, observed_at):
                        raise PlatformMembershipRemovalError('Construction grant list is invalid.')
                    before[row.id] = history
                locked = []
                for identifier in sorted(before, key=lambda v: v.int):
                    row = await self.grants.get_grant_for_update(tenant_id=actor.tenant_id, grant_id=identifier)
                    self._authority(capture, actor, user_id)
                    if self._grant(row, actor, user_id) != before[identifier]:
                        raise PlatformMembershipRemovalError('Construction grant history changed while locking.')
                    if row.status == 'revoked':
                        if not isinstance(row.revoked_by_user_id, UUID) or row.revoked_at is None:
                            raise PlatformMembershipRemovalError('Construction grant revocation history is invalid.')
                        continue
                    if row.status != 'active' or row.revoked_at is not None or row.revoked_by_user_id is not None:
                        raise PlatformMembershipRemovalError('Construction grant lifecycle is invalid.')
                    locked.append(row)
                instant = self._authority(capture, actor, user_id)
                expected = {row.id for row in locked if self._active(row, instant)}
                rows = await self.grants.revoke_effective_user_grants(tenant_id=actor.tenant_id, user_id=user_id,
                    revoked_by_user_id=actor.user_id, as_of=instant)
                self._authority(capture, actor, user_id)
                if not isinstance(rows, (tuple, list)):
                    raise PlatformMembershipRemovalError('Construction grant revocation receipt is invalid.')
                changed = {}
                for row in rows:
                    if (self._grant(row, actor, user_id) != before.get(row.id) or row.id in changed or row.id not in expected
                            or row.status != 'revoked' or row.revoked_by_user_id != actor.user_id or row.revoked_at != instant):
                        raise PlatformMembershipRemovalError('Construction grant revocation receipt is invalid.')
                    changed[row.id] = row
                if set(changed) != expected:
                    raise PlatformMembershipRemovalError('Construction grant revocation set differs.')
                audit_ids = []
                for identifier in sorted(changed, key=lambda v: v.int):
                    row = changed[identifier]
                    changes = (ConstructionAuditChange('status', 'active', 'revoked'),
                        ConstructionAuditChange('revoked_by_user_id', None, str(actor.user_id)),
                        ConstructionAuditChange('revoked_at', None, instant.isoformat()))
                    details = dict(target_user_id=str(user_id), role=row.role, scope_type=row.scope_type,
                        scope_id=str(row.scope_id), team_action='remove_from_organization')
                    event = await self.audit.record_operation(actor=actor, event_type='construction_access_grant_revoked',
                        entity_type='construction_access_grant', entity_id=identifier, operation='revoke',
                        changes=changes, details=details, trace_id=trace_id)
                    self._authority(capture, actor, user_id)
                    payload = dict(operation='revoke', changes=[change.as_payload() for change in changes], reason=None, **details)
                    if (not isinstance(getattr(event, 'id', None), UUID) or event.id in audit_ids
                            or event.tenant_id != actor.tenant_id or event.user_id != actor.user_id
                            or event.entity_id != identifier or event.entity_type != 'construction_access_grant'
                            or event.event_type != 'construction_access_grant_revoked' or event.platform != 'api'
                            or event.payload != payload or (trace_id is not None and event.trace_id != trace_id)):
                        raise PlatformMembershipRemovalError('Mandatory Construction revocation audit receipt is invalid.')
                    audit_ids.append(event.id)
                remaining = await self.grants.list_active_grants(tenant_id=actor.tenant_id, user_id=user_id,
                    as_of=self._authority(capture, actor, user_id))
                self._authority(capture, actor, user_id)
                if not isinstance(remaining, (tuple, list)) or remaining:
                    raise PlatformMembershipRemovalError('Effective Construction access remains after removal.')
                if await self.grants.has_active_platform_membership(tenant_id=actor.tenant_id, user_id=user_id) is not False:
                    raise PlatformMembershipRemovalError('Platform membership remains after removal.')
                self._authority(capture, actor, user_id)
                if self.session.in_transaction() is not True:
                    raise PlatformMembershipRemovalError('Organization removal transaction ended prematurely.')
                return ConstructionOrganizationRemovalResult(membership,
                    tuple(sorted(changed, key=lambda v: v.int)), tuple(audit_ids))
        except (PlatformMembershipRemovalDeniedError, PlatformMembershipRemovalError, ValueError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise PlatformMembershipRemovalError('Organization removal failed.') from None
        except BaseException:
            await self.session.rollback()
            raise


def build_construction_organization_removal_service(*, session, authorization_service, now_provider=None):
    from database.repositories.platform_membership import PlatformMembershipRepository
    from database.repositories.construction_team_access_mutations import ConstructionTeamAccessMutationRepository
    from database.repositories.event import EventRepository
    return ConstructionOrganizationRemovalService(session=session, membership_repository=PlatformMembershipRepository(session),
        grant_repository=ConstructionTeamAccessMutationRepository(session), event_repository=EventRepository(session),
        authorization_service=authorization_service, now_provider=now_provider)
