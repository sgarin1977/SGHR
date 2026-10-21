from datetime import datetime, timezone
from uuid import UUID
from services.construction_invitations import ConstructionInvitationOperationError, ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, require_construction_invitation_email, require_construction_invitation_token
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_role_scope

class ConstructionInvitationPreviewService:
    """Read one invitation/scopes snapshot; mutations recheck under their lock."""

    def __init__(self, *, session, repository, identity_reader, now_provider=None):
        if session is None or getattr(repository, 'session', None) is not session or getattr(identity_reader, 'session', None) is session or (not callable(getattr(repository, 'get_snapshot', None))) or (not callable(getattr(identity_reader, 'has_verified_email', None))) or (not callable(getattr(session, 'in_transaction', None))) or (now_provider is not None and (not callable(now_provider))):
            raise ValueError('Preview requires isolated identity and Construction sessions.')
        self.session = session
        self.repository = repository
        self.identity_reader = identity_reader
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def preview(self, *, actor, tenant_id, invitation_id, token):
        try:
            if getattr(actor, 'status', None) != 'active' or not isinstance(getattr(actor, 'user_id', None), UUID):
                raise ConstructionInvitationRecipientDeniedError('Invitation recipient is not available.')
            if not isinstance(tenant_id, UUID) or not isinstance(invitation_id, UUID) or (not self.session.in_transaction()):
                raise ConstructionInvitationTokenDeniedError('Invitation context is not available.')
            snapshot = await self.repository.get_snapshot(tenant_id=tenant_id, invitation_id=invitation_id)
            invitation = getattr(snapshot, 'invitation', None)
            if invitation is None or invitation.id != invitation_id or invitation.tenant_id != tenant_id:
                raise ConstructionInvitationTokenDeniedError('Invitation is not available.')
            if getattr(invitation, 'user_id', None) not in (None, actor.user_id):
                raise ConstructionInvitationRecipientDeniedError('Invitation recipient is not available.')
            require_construction_invitation_token(invitation=invitation, token=token, as_of=self.now_provider())
            await require_construction_invitation_email(actor=actor, invitation_email=invitation.email, identity_reader=self.identity_reader)
            require_construction_invitation_token(invitation=invitation, token=token, as_of=self.now_provider())
            scopes = getattr(snapshot, 'scopes', None)
            if not isinstance(scopes, tuple) or not scopes or len(set(scopes)) != len(scopes):
                raise ConstructionScopeDeniedError('Invitation scopes are not available.')
            for kind, target in scopes:
                if kind not in ('tenant', 'project') or not isinstance(target, UUID) or (kind == 'tenant' and target != tenant_id):
                    raise ConstructionScopeDeniedError('Invitation scope is not available.')
                require_construction_role_scope(role=invitation.role, scope_type=kind)
            version = invitation.row_version
            if type(version) is not int or version <= 0:
                raise ConstructionInvitationOperationError('Invitation version is invalid.')
            deadline = invitation.grant_expires_at
            if deadline is not None and (not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None):
                raise ConstructionInvitationOperationError('Grant expiration is invalid.')
            return {'id': str(invitation_id), 'role': invitation.role, 'status': 'pending', 'scopes': [{'scope_type': kind, 'scope_id': str(target)} for kind, target in scopes], 'expires_at': invitation.expires_at.isoformat(), 'grant_expires_at': deadline.isoformat() if deadline is not None else None, 'row_version': version}
        except (ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, ConstructionInvitationOperationError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionInvitationOperationError('Invitation preview failed.') from None

def build_construction_invitation_preview_service(*, session, identity_reader, now_provider=None):
    from database.repositories.construction_invitation_preview import ConstructionInvitationPreviewRepository
    return ConstructionInvitationPreviewService(session=session, repository=ConstructionInvitationPreviewRepository(session), identity_reader=identity_reader, now_provider=now_provider)
