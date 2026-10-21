from datetime import datetime
from uuid import UUID
from database.construction import set_construction_tenant_context
from database.repositories.user import PlatformInvitationMembershipConflictError, PlatformInvitationMembershipUnavailableError
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_invitation_workflows import ConstructionInvitationIdempotencyInProgressError
from services.construction_invitations import ConstructionInvitationOperationError, ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, ConstructionInvitationValidationError, hash_construction_invitation_token, verify_construction_invitation_token, require_construction_invitation_email
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError

class ConstructionInvitationAcceptanceWorkflow:
    """Trusted server tenant context; one commit for acceptance and idempotency."""

    def __init__(self, *, session, acceptance_service, idempotency_service):
        context = getattr(acceptance_service, 'context', None)
        collaborators = (acceptance_service, context, getattr(context, 'repository', None), getattr(acceptance_service, 'membership_repository', None), getattr(acceptance_service, 'grant_repository', None), getattr(acceptance_service, 'project_repository', None), getattr(getattr(acceptance_service, 'audit_logger', None), 'event_repository', None), getattr(idempotency_service, 'repository', None))
        methods = ((session, 'commit'), (session, 'rollback'), (session, 'execute'), (session, 'in_transaction'), (acceptance_service, 'accept_invitation'), (idempotency_service, 'reserve'), (idempotency_service, 'complete'), (getattr(context, 'repository', None), 'get_invitation_for_update'), (getattr(context, 'identity_reader', None), 'has_verified_email'), (context, 'now_provider'))
        if session is None or any((getattr(item, 'session', None) is not session for item in collaborators)) or any((not callable(getattr(item, name, None)) for item, name in methods)) or (getattr(getattr(context, 'identity_reader', None), 'session', None) is session):
            raise ValueError('Acceptance workflow requires one shared write session.')
        self.session = session
        self.acceptance_service = acceptance_service
        self.idempotency_service = idempotency_service

    def _now(self):
        now = self.acceptance_service.context.now_provider()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ConstructionInvitationValidationError('Invitation time is invalid.')
        return now

    async def accept_invitation(self, *, actor, tenant_id, invitation_id, expected_row_version, token, idempotency_key, trace_id=None):
        try:
            if getattr(actor, 'status', None) != 'active' or not isinstance(getattr(actor, 'user_id', None), UUID):
                raise ConstructionInvitationRecipientDeniedError('Invitation recipient is not available.')
            if not isinstance(tenant_id, UUID) or not isinstance(invitation_id, UUID) or type(expected_row_version) is not int or (expected_row_version <= 0) or (not isinstance(idempotency_key, str)) or (not idempotency_key.strip()) or (not self.session.in_transaction()):
                raise ConstructionInvitationValidationError('Invitation context, version and idempotency key are required.')
            if not isinstance(token, str) or not token:
                raise ConstructionInvitationTokenDeniedError('Invitation token is not available.')
            await set_construction_tenant_context(self.session, tenant_id=tenant_id)
            reservation = await self.idempotency_service.reserve(tenant_id=tenant_id, principal_type='user', principal_id=actor.user_id, operation='construction.invitation.accept', idempotency_key=idempotency_key, payload={'invitation_id': str(invitation_id), 'expected_row_version': expected_row_version, 'token_hash': hash_construction_invitation_token(token)}, now=self._now())
            record = reservation.record
            if getattr(record, 'tenant_id', None) != tenant_id or getattr(record, 'principal_type', None) != 'user' or getattr(record, 'principal_id', None) != actor.user_id or (getattr(record, 'operation', None) != 'construction.invitation.accept'):
                raise ConstructionInvitationOperationError('Idempotency context is invalid.')
            if reservation.is_replay:
                if reservation.response_status is None:
                    raise ConstructionInvitationIdempotencyInProgressError('Invitation operation is still processing.')
                saved = reservation.response_payload
                if reservation.response_status != 200 or not isinstance(saved, dict) or set(saved) != {'id', 'status', 'row_version'} or (saved.get('id') != str(invitation_id)) or (saved.get('status') != 'accepted') or (type(saved.get('row_version')) is not int) or (saved['row_version'] != expected_row_version + 1):
                    raise ConstructionInvitationOperationError('Stored acceptance response is invalid.')
                context = self.acceptance_service.context
                invitation = await context.repository.get_invitation_for_update(tenant_id=tenant_id, invitation_id=invitation_id)
                if invitation is None or getattr(invitation, 'id', None) != invitation_id or getattr(invitation, 'tenant_id', None) != tenant_id or (getattr(invitation, 'user_id', None) != actor.user_id) or (getattr(invitation, 'status', None) != 'accepted') or (getattr(invitation, 'row_version', None) != saved['row_version']) or (getattr(invitation, 'token_used_at', None) is None) or (not verify_construction_invitation_token(token=token, expected_hash=getattr(invitation, 'token_hash', None))):
                    raise ConstructionInvitationTokenDeniedError('Accepted invitation is not available.')
                await require_construction_invitation_email(actor=actor, invitation_email=invitation.email, identity_reader=context.identity_reader)
                response = dict(saved)
            else:
                invitation = await self.acceptance_service.accept_invitation(actor=actor, tenant_id=tenant_id, invitation_id=invitation_id, expected_row_version=expected_row_version, token=token, trace_id=trace_id)
                version = getattr(invitation, 'row_version', None)
                if getattr(invitation, 'id', None) != invitation_id or getattr(invitation, 'tenant_id', None) != tenant_id or getattr(invitation, 'user_id', None) != actor.user_id or (getattr(invitation, 'status', None) != 'accepted') or (getattr(invitation, 'token_used_at', None) is None) or (type(version) is not int) or (version != expected_row_version + 1):
                    raise ConstructionInvitationOperationError('Accepted invitation state is invalid.')
                response = {'id': str(invitation_id), 'status': 'accepted', 'row_version': version}
                await self.idempotency_service.complete(reservation=reservation, response_status=200, response_payload=response, now=self._now())
            await self.session.commit()
            return response
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, (ConstructionInvitationOperationError, ConstructionInvitationRecipientDeniedError, ConstructionInvitationTokenDeniedError, ConstructionInvitationValidationError, ConstructionRowVersionConflictError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError, PlatformInvitationMembershipConflictError, PlatformInvitationMembershipUnavailableError, ApiIdempotencyKeyReusedError)):
                raise
            raise ConstructionInvitationOperationError('Invitation acceptance workflow failed.') from None

def build_construction_invitation_acceptance_workflow(*, session, identity_reader, idempotency_encryption_key, now_provider=None):
    from services.api_idempotency import build_api_idempotency_service
    from services.construction_invitation_acceptance import build_construction_invitation_acceptance_service
    from services.construction_membership_writers import SerializedConstructionInvitationAcceptanceWorkflow
    return SerializedConstructionInvitationAcceptanceWorkflow(session=session, acceptance_service=build_construction_invitation_acceptance_service(session=session, identity_reader=identity_reader, now_provider=now_provider), idempotency_service=build_api_idempotency_service(session, encryption_key=idempotency_encryption_key))
