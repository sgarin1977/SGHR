from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy.orm.exc import StaleDataError
from database.repositories.user import PlatformInvitationMembershipConflictError, PlatformInvitationMembershipUnavailableError
from services.construction.construction_audit import ConstructionAuditChange, ConstructionAuditLogger
from services.construction.construction_foundation import ConstructionRowVersionConflictError
from services.construction.construction_invitations import ConstructionInvitationOperationError, ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, ConstructionInvitationValidationError, require_construction_invitation_token
from services.construction.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_role_scope

class ConstructionInvitationAcceptanceService:
    """Internal acceptance writes; the idempotency workflow owns the commit.

    actor is authenticated Platform identity. tenant_id is trusted server
    invitation context, not a claim of recipient membership. Email proof
    uses a separate Platform session. All writes share the context session.
    """

    def __init__(self, *, session, context, membership_repository, grant_repository, project_repository, event_repository):
        collaborators = (context, getattr(context, 'repository', None), membership_repository, grant_repository, project_repository, event_repository)
        methods = ((context, 'prepare_pending_acceptance'), (getattr(context, 'repository', None), 'list_invitation_scopes'), (getattr(context, 'repository', None), 'accept_invitation'), (membership_repository, 'ensure_invitation_tenant_membership'), (grant_repository, 'create_grant'), (project_repository, 'get_active_project_for_update'), (event_repository, 'create_event'), (session, 'rollback'), (session, 'in_transaction'), (context, 'now_provider'))
        if session is None or any((getattr(item, 'session', None) is not session for item in collaborators)) or any((not callable(getattr(item, name, None)) for item, name in methods)):
            raise ValueError('Acceptance requires one shared write session.')
        self.session = session
        self.context = context
        self.membership_repository = membership_repository
        self.grant_repository = grant_repository
        self.project_repository = project_repository
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)

    def _check_time(self, invitation):
        now = self.context.now_provider()
        deadline = invitation.grant_expires_at
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or (deadline is not None and (not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None or (deadline <= now))):
            raise ConstructionInvitationValidationError('Grant expiration is invalid.')
        return now

    async def accept_invitation(self, *, actor, tenant_id, invitation_id, expected_row_version, token, trace_id=None):
        try:
            invitation = await self.context.prepare_pending_acceptance(actor=actor, tenant_id=tenant_id, invitation_id=invitation_id, expected_row_version=expected_row_version, token=token)
            if getattr(invitation, 'user_id', None) not in (None, actor.user_id):
                raise ConstructionInvitationRecipientDeniedError('Invitation recipient is not available.')
            if not isinstance(invitation.created_by_user_id, UUID):
                raise ConstructionInvitationValidationError('Invitation creator is invalid.')
            self._check_time(invitation)
            rows = await self.context.repository.list_invitation_scopes(tenant_id=tenant_id, invitation_id=invitation_id)
            if not isinstance(rows, (list, tuple)) or not rows:
                raise ConstructionScopeDeniedError('Invitation scopes are not available.')
            scopes = []
            for row in rows:
                kind = getattr(row, 'scope_type', None)
                scope_id = getattr(row, 'scope_id', None)
                if getattr(row, 'tenant_id', None) != tenant_id or getattr(row, 'invitation_id', None) != invitation_id or kind not in ('tenant', 'project') or (not isinstance(scope_id, UUID)) or (kind == 'tenant' and scope_id != tenant_id) or ((kind, scope_id) in scopes):
                    raise ConstructionScopeDeniedError('Invitation scope is invalid.')
                require_construction_role_scope(role=invitation.role, scope_type=kind)
                scopes.append((kind, scope_id))
            scopes.sort(key=lambda item: (item[0], str(item[1])))
            for kind, scope_id in scopes:
                if kind != 'project':
                    continue
                project = await self.project_repository.get_active_project_for_update(tenant_id=tenant_id, project_id=scope_id)
                if project is None or project.id != scope_id or project.tenant_id != tenant_id or (getattr(project, 'deleted_at', True) is not None):
                    raise ConstructionScopeDeniedError('Invitation project is not available.')
            await self.membership_repository.ensure_invitation_tenant_membership(user_id=actor.user_id, tenant_id=tenant_id)
            accepted_at = self._check_time(invitation)
            require_construction_invitation_token(invitation=invitation, token=token, as_of=accepted_at)
            audit_actor = SimpleNamespace(tenant_id=tenant_id, user_id=actor.user_id)
            grant_ids = []
            for kind, scope_id in scopes:
                grant = await self.grant_repository.create_grant(tenant_id=tenant_id, user_id=actor.user_id, role=invitation.role, scope_type=kind, scope_id=scope_id, expires_at=invitation.grant_expires_at, granted_by_user_id=invitation.created_by_user_id, granted_at=accepted_at)
                grant_ids.append(str(grant.id))
                await self.audit_logger.record_operation(actor=audit_actor, event_type='construction_access_grant_created', entity_type='construction_access_grant', entity_id=grant.id, operation='create', details={'target_user_id': str(actor.user_id), 'role': invitation.role, 'scope_type': kind, 'scope_id': str(scope_id), 'expires_at': invitation.grant_expires_at.isoformat() if invitation.grant_expires_at is not None else None, 'invitation_id': str(invitation_id)}, trace_id=trace_id)
            invitation = await self.context.repository.accept_invitation(invitation=invitation, user_id=actor.user_id, accepted_at=accepted_at)
            await self.audit_logger.record_operation(actor=audit_actor, event_type='construction_invitation_accepted', entity_type='construction_invitation', entity_id=invitation_id, operation='accept', changes=(ConstructionAuditChange(field='status', old_value='pending', new_value='accepted'),), details={'grant_ids': grant_ids}, trace_id=trace_id)
            return invitation
        except StaleDataError:
            await self.session.rollback()
            raise ConstructionRowVersionConflictError() from None
        except (ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, ConstructionInvitationValidationError, ConstructionRowVersionConflictError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError, PlatformInvitationMembershipConflictError, PlatformInvitationMembershipUnavailableError):
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise ConstructionInvitationOperationError('Invitation acceptance failed.') from None

def build_construction_invitation_acceptance_service(*, session, identity_reader, now_provider=None):
    from database.repositories.construction import ConstructionAccessRepository, ConstructionProjectRepository
    from database.repositories.event import EventRepository
    from database.repositories.user import UserRepository
    from services.construction.construction_invitation_acceptance_context import build_construction_invitation_acceptance_context
    from services.construction.construction_membership_writers import SerializedConstructionInvitationAcceptanceService
    return SerializedConstructionInvitationAcceptanceService(session=session, context=build_construction_invitation_acceptance_context(session=session, identity_reader=identity_reader, now_provider=now_provider), membership_repository=UserRepository(session), grant_repository=ConstructionAccessRepository(session), project_repository=ConstructionProjectRepository(session), event_repository=EventRepository(session))
