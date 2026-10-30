from datetime import datetime

from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_invitations import (
    ConstructionInvitationOperationError,
    ConstructionInvitationValidationError,
    normalize_construction_invitation_email,
    require_construction_invitation_permissions,
)
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)


class ConstructionInvitationIdempotencyInProgressError(
    ConstructionInvitationOperationError
):
    status_code = 409
    code = "idempotency_in_progress"


class ConstructionInvitationWorkflow:
    def __init__(
        self, *, session, invitation_service, idempotency_service,
        delivery_queue=None,
    ):
        if (
            session is None
            or getattr(invitation_service, "session", None) is not session
            or getattr(
                getattr(idempotency_service, "repository", None), "session", None
            ) is not session
            or (
                delivery_queue is not None
                and getattr(delivery_queue, "session", None) is not session
            )
        ):
            raise ValueError("Invitation workflow requires one shared session.")
        self.session = session
        self.invitation_service = invitation_service
        self.idempotency_service = idempotency_service
        self.delivery_queue = delivery_queue

    async def create_invitation(
        self, *, actor, access_context, idempotency_key, email, role, scopes,
        expires_at=None, grant_expires_at=None, trace_id=None,
    ):
        try:
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ConstructionInvitationValidationError("Idempotency key is required.")
            normalized_role = role.strip().upper() if isinstance(role, str) else ""
            require_construction_invitation_permissions(
                actor=actor, access_context=access_context,
                role=normalized_role, scopes=scopes,
            )
            normalized_email = normalize_construction_invitation_email(email)
            normalized_scopes = sorted(
                {(kind, scope_id) for kind, scope_id in scopes},
                key=lambda scope: (scope[0], str(scope[1])),
            )
            for deadline in (expires_at, grant_expires_at):
                if deadline is not None and (
                    not isinstance(deadline, datetime)
                    or deadline.tzinfo is None or deadline.utcoffset() is None
                ):
                    raise ConstructionInvitationValidationError("Expiration is invalid.")
            scope_data = [
                {"scope_type": kind, "scope_id": str(scope_id)}
                for kind, scope_id in normalized_scopes
            ]
            reservation = await self.idempotency_service.reserve(
                tenant_id=actor.tenant_id,
                principal_type="user",
                principal_id=actor.user_id,
                operation="construction.invitation.create",
                idempotency_key=idempotency_key,
                payload={
                    "email": normalized_email, "role": normalized_role,
                    "scopes": scope_data,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                    "grant_expires_at": (
                        grant_expires_at.isoformat() if grant_expires_at else None
                    ),
                },
            )
            if reservation.is_replay:
                if reservation.response_status is None:
                    raise ConstructionInvitationIdempotencyInProgressError(
                        "Invitation operation is still processing."
                    )
                if (
                    reservation.response_status != 201
                    or not isinstance(reservation.response_payload, dict)
                ):
                    raise ConstructionInvitationOperationError(
                        "Stored invitation response is invalid."
                    )
                response = dict(reservation.response_payload)
                await self.session.commit()
                return response

            if self.delivery_queue is None:
                raise ConstructionInvitationOperationError(
                    "Invitation delivery queue is not configured."
                )
            creation = await self.invitation_service.create_invitation(
                actor=actor, access_context=access_context,
                email=normalized_email, role=normalized_role,
                scopes=normalized_scopes,
                expires_at=expires_at, grant_expires_at=grant_expires_at,
                trace_id=trace_id,
            )
            await self.delivery_queue.enqueue_invitation(
                actor=actor, invitation=creation.invitation,
                token=creation.token, trace_id=trace_id,
            )
            invitation = creation.invitation
            response = {
                "id": str(invitation.id),
                "email": invitation.email,
                "role": invitation.role,
                "status": invitation.status,
                "scopes": scope_data,
                "expires_at": invitation.expires_at.isoformat(),
                "grant_expires_at": (
                    invitation.grant_expires_at.isoformat()
                    if invitation.grant_expires_at else None
                ),
                "row_version": invitation.row_version,
            }
            await self.idempotency_service.complete(
                reservation=reservation, response_status=201,
                response_payload=response,
            )
            await self.session.commit()
            return response
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, (
                ConstructionInvitationOperationError,
                ConstructionInvitationValidationError,
                ConstructionPermissionDeniedError,
                ConstructionScopeDeniedError,
                ApiIdempotencyKeyReusedError,
            )):
                raise
            raise ConstructionInvitationOperationError(
                "Construction invitation workflow failed."
            ) from exc

    async def resend_invitation(
        self, *, actor, access_context, invitation_id,
        expected_row_version, idempotency_key,
        expires_at=None, trace_id=None,
    ):
        from uuid import UUID
        from services.construction.construction_foundation import (
            ConstructionRowVersionConflictError,
        )
        from services.construction.construction_invitations import (
            ConstructionInvitationTransitionError,
        )

        try:
            if (
                getattr(actor, "status", None) != "active"
                or not isinstance(getattr(actor, "user_id", None), UUID)
                or not isinstance(getattr(actor, "tenant_id", None), UUID)
                or getattr(access_context, "user_id", None) != actor.user_id
                or getattr(access_context, "tenant_id", None) != actor.tenant_id
            ):
                raise ConstructionPermissionDeniedError(
                    "Invitation actor is not available."
                )
            if (
                not isinstance(idempotency_key, str)
                or not idempotency_key.strip()
                or not isinstance(invitation_id, UUID)
                or type(expected_row_version) is not int
                or expected_row_version <= 0
            ):
                raise ConstructionInvitationValidationError(
                    "Invitation ID, version and idempotency key are required."
                )
            if expires_at is not None and (
                not isinstance(expires_at, datetime)
                or expires_at.tzinfo is None
                or expires_at.utcoffset() is None
            ):
                raise ConstructionInvitationValidationError(
                    "Expiration must be timezone-aware."
                )

            reservation = await self.idempotency_service.reserve(
                tenant_id=actor.tenant_id,
                principal_type="user",
                principal_id=actor.user_id,
                operation="construction.invitation.resend",
                idempotency_key=idempotency_key,
                payload={
                    "invitation_id": str(invitation_id),
                    "expected_row_version": expected_row_version,
                    "expires_at": (
                        expires_at.isoformat() if expires_at else None
                    ),
                },
            )
            if reservation.is_replay:
                if reservation.response_status is None:
                    raise ConstructionInvitationIdempotencyInProgressError(
                        "Invitation operation is still processing."
                    )
                saved = reservation.response_payload
                if (
                    reservation.response_status != 200
                    or not isinstance(saved, dict)
                    or saved.get("id") != str(invitation_id)
                    or not isinstance(saved.get("role"), str)
                    or not isinstance(saved.get("scopes"), list)
                    or not saved["scopes"]
                ):
                    raise ConstructionInvitationOperationError(
                        "Stored invitation response is invalid."
                    )
                try:
                    scopes = [
                        (item["scope_type"], UUID(item["scope_id"]))
                        for item in saved["scopes"]
                    ]
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    raise ConstructionInvitationOperationError(
                        "Stored invitation scopes are invalid."
                    ) from exc
                require_construction_invitation_permissions(
                    actor=actor, access_context=access_context,
                    role=saved["role"], scopes=scopes,
                )
                await self.session.commit()
                return dict(saved)

            if self.delivery_queue is None:
                raise ConstructionInvitationOperationError(
                    "Invitation delivery queue is not configured."
                )
            result = await self.invitation_service.resend_invitation(
                actor=actor, access_context=access_context,
                invitation_id=invitation_id,
                expected_row_version=expected_row_version,
                expires_at=expires_at, trace_id=trace_id,
            )
            invitation = result.invitation
            if (
                invitation.id != invitation_id
                or invitation.tenant_id != actor.tenant_id
            ):
                raise ConstructionScopeDeniedError(
                    "Invitation is not available."
                )
            rows = await self.invitation_service.repository.list_invitation_scopes(
                tenant_id=actor.tenant_id, invitation_id=invitation_id,
            )
            if not rows or any(
                row.tenant_id != actor.tenant_id
                or row.invitation_id != invitation_id
                for row in rows
            ):
                raise ConstructionScopeDeniedError(
                    "Invitation scopes are not available."
                )
            scopes = [(row.scope_type, row.scope_id) for row in rows]
            require_construction_invitation_permissions(
                actor=actor, access_context=access_context,
                role=invitation.role, scopes=scopes,
            )
            await self.delivery_queue.enqueue_invitation(
                actor=actor, invitation=invitation,
                token=result.token, trace_id=trace_id,
            )
            response = {
                "id": str(invitation.id),
                "email": invitation.email,
                "role": invitation.role,
                "status": invitation.status,
                "scopes": [
                    {"scope_type": kind, "scope_id": str(scope_id)}
                    for kind, scope_id in scopes
                ],
                "expires_at": invitation.expires_at.isoformat(),
                "grant_expires_at": (
                    invitation.grant_expires_at.isoformat()
                    if invitation.grant_expires_at else None
                ),
                "row_version": invitation.row_version,
            }
            await self.idempotency_service.complete(
                reservation=reservation,
                response_status=200, response_payload=response,
            )
            await self.session.commit()
            return response
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, (
                ConstructionInvitationOperationError,
                ConstructionInvitationValidationError,
                ConstructionInvitationTransitionError,
                ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError,
                ConstructionScopeDeniedError,
                ApiIdempotencyKeyReusedError,
            )):
                raise
            raise ConstructionInvitationOperationError(
                "Invitation resend workflow failed."
            ) from exc

    async def revoke_invitation(
        self, *, actor, access_context, invitation_id,
        expected_row_version, idempotency_key, trace_id=None,
    ):
        from uuid import UUID
        from services.construction.construction_foundation import (
            ConstructionRowVersionConflictError,
        )
        from services.construction.construction_invitations import (
            ConstructionInvitationTransitionError,
        )

        try:
            if (
                getattr(actor, "status", None) != "active"
                or not isinstance(getattr(actor, "user_id", None), UUID)
                or not isinstance(getattr(actor, "tenant_id", None), UUID)
                or getattr(access_context, "user_id", None) != actor.user_id
                or getattr(access_context, "tenant_id", None) != actor.tenant_id
            ):
                raise ConstructionPermissionDeniedError(
                    "Invitation actor is not available."
                )
            if (
                not isinstance(idempotency_key, str)
                or not idempotency_key.strip()
                or not isinstance(invitation_id, UUID)
                or type(expected_row_version) is not int
                or expected_row_version <= 0
            ):
                raise ConstructionInvitationValidationError(
                    "Invitation ID, version and idempotency key are required."
                )

            reservation = await self.idempotency_service.reserve(
                tenant_id=actor.tenant_id,
                principal_type="user",
                principal_id=actor.user_id,
                operation="construction.invitation.revoke",
                idempotency_key=idempotency_key,
                payload={
                    "invitation_id": str(invitation_id),
                    "expected_row_version": expected_row_version,
                },
            )
            if reservation.is_replay:
                if reservation.response_status is None:
                    raise ConstructionInvitationIdempotencyInProgressError(
                        "Invitation operation is still processing."
                    )
                saved = reservation.response_payload
                public_keys = {
                    "id", "email", "role", "status", "scopes",
                    "expires_at", "grant_expires_at", "row_version",
                }
                if (
                    reservation.response_status != 200
                    or not isinstance(saved, dict)
                    or set(saved) != public_keys
                    or saved.get("id") != str(invitation_id)
                    or saved.get("status") != "revoked"
                    or not isinstance(saved.get("role"), str)
                    or type(saved.get("row_version")) is not int
                    or saved["row_version"] <= 0
                    or not isinstance(saved.get("scopes"), list)
                    or not saved["scopes"]
                ):
                    raise ConstructionInvitationOperationError(
                        "Stored invitation response is invalid."
                    )
                try:
                    if any(
                        not isinstance(item, dict)
                        or set(item) != {"scope_type", "scope_id"}
                        for item in saved["scopes"]
                    ):
                        raise ValueError("Invalid stored scopes.")
                    scopes = [
                        (item["scope_type"], UUID(item["scope_id"]))
                        for item in saved["scopes"]
                    ]
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    raise ConstructionInvitationOperationError(
                        "Stored invitation scopes are invalid."
                    ) from exc
                require_construction_invitation_permissions(
                    actor=actor, access_context=access_context,
                    role=saved["role"], scopes=scopes,
                )
                await self.session.commit()
                return dict(saved)

            invitation = await self.invitation_service.revoke_invitation(
                actor=actor, access_context=access_context,
                invitation_id=invitation_id,
                expected_row_version=expected_row_version,
                trace_id=trace_id,
            )
            if (
                getattr(invitation, "id", None) != invitation_id
                or getattr(invitation, "tenant_id", None) != actor.tenant_id
                or getattr(invitation, "status", None) != "revoked"
            ):
                raise ConstructionScopeDeniedError(
                    "Invitation is not available."
                )

            rows = await self.invitation_service.repository.list_invitation_scopes(
                tenant_id=actor.tenant_id, invitation_id=invitation_id,
            )
            if (
                not isinstance(rows, (list, tuple))
                or not rows
                or any(
                    getattr(row, "tenant_id", None) != actor.tenant_id
                    or getattr(row, "invitation_id", None) != invitation_id
                    for row in rows
                )
            ):
                raise ConstructionScopeDeniedError(
                    "Invitation scopes are not available."
                )
            scopes = [
                (getattr(row, "scope_type", None), getattr(row, "scope_id", None))
                for row in rows
            ]
            require_construction_invitation_permissions(
                actor=actor, access_context=access_context,
                role=invitation.role, scopes=scopes,
            )
            response = {
                "id": str(invitation.id),
                "email": invitation.email,
                "role": invitation.role,
                "status": invitation.status,
                "scopes": [
                    {"scope_type": kind, "scope_id": str(scope_id)}
                    for kind, scope_id in scopes
                ],
                "expires_at": invitation.expires_at.isoformat(),
                "grant_expires_at": (
                    invitation.grant_expires_at.isoformat()
                    if invitation.grant_expires_at else None
                ),
                "row_version": invitation.row_version,
            }
            await self.idempotency_service.complete(
                reservation=reservation,
                response_status=200, response_payload=response,
            )
            await self.session.commit()
            return response
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, (
                ConstructionInvitationOperationError,
                ConstructionInvitationValidationError,
                ConstructionInvitationTransitionError,
                ConstructionRowVersionConflictError,
                ConstructionPermissionDeniedError,
                ConstructionScopeDeniedError,
                ApiIdempotencyKeyReusedError,
            )):
                raise
            raise ConstructionInvitationOperationError(
                "Invitation revoke workflow failed."
            ) from exc



def build_construction_invitation_workflow(
    *, session, idempotency_encryption_key: str,
    delivery_encryption_key: str, now_provider=None, token_provider=None,
):
    from database.repositories.construction import (
        ConstructionInvitationRepository,
        ConstructionProjectRepository,
    )
    from database.repositories.event import EventRepository
    from services.api_idempotency import build_api_idempotency_service
    from services.construction.construction_invitation_delivery import (
        build_construction_invitation_delivery_queue,
    )
    from services.construction.construction_invitations import ConstructionInvitationService

    return ConstructionInvitationWorkflow(
        session=session,
        invitation_service=ConstructionInvitationService(
            session=session,
            repository=ConstructionInvitationRepository(session),
            project_repository=ConstructionProjectRepository(session),
            event_repository=EventRepository(session),
            now_provider=now_provider,
            token_provider=token_provider,
        ),
        idempotency_service=build_api_idempotency_service(
            session, encryption_key=idempotency_encryption_key,
        ),
        delivery_queue=build_construction_invitation_delivery_queue(
            session=session,
            encryption_key=delivery_encryption_key,
            now_provider=now_provider,
        ),
    )
