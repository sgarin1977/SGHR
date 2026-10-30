from datetime import datetime, timezone
from uuid import UUID

from services.api_idempotency import ApiIdempotencyKeyReusedError, build_api_idempotency_service
from services.construction.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction.construction_access_issuance import build_construction_access_issuance_service
from services.construction.construction_access_mutations import is_active_grant_overlap
from services.construction.construction_access_workflows import ConstructionAccessIdempotencyInProgressError, PUBLIC_GRANT_FIELDS
from services.construction.construction_grants import ConstructionGrantOperationError
from services.construction.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError, require_construction_role_scope,
)


class ConstructionAccessIssuanceWorkflow:
    """One commit for issuance, audit and optional encrypted response replay."""

    def __init__(self, *, session, issuance_service, idempotency_service=None):
        collaborators = (
            issuance_service, getattr(issuance_service, "repository", None),
            getattr(issuance_service, "project_repository", None),
            getattr(getattr(issuance_service, "audit_logger", None), "event_repository", None),
            getattr(getattr(issuance_service, "resolver", None), "repository", None),
        )
        if (
            session is None or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or any(not callable(getattr(issuance_service, name, None)) for name in (
                "grant_access", "_authorize", "_now",
            ))
            or (idempotency_service is not None and (
                getattr(getattr(idempotency_service, "repository", None), "session", None) is not session
                or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
            ))
        ):
            raise ValueError("Grant issuance workflow requires valid shared-session wiring.")
        self.session = session
        self.service = issuance_service
        self.idempotency = idempotency_service

    @staticmethod
    def _iso(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ConstructionGrantOperationError("Stored grant time is invalid.")
        return value.astimezone(timezone.utc).isoformat()

    def _public(self, grant, *, actor, target_user_id, role, scope_type, scope_id, expires_at, replay=False):
        if (
            grant is None or not isinstance(getattr(grant, "id", None), UUID)
            or grant.tenant_id != actor.tenant_id or grant.user_id != target_user_id
            or grant.granted_by_user_id != actor.user_id
            or grant.role != role or grant.scope_type != scope_type or grant.scope_id != scope_id
            or grant.expires_at != expires_at
            or grant.status not in (("active", "revoked") if replay else ("active",))
        ):
            raise ConstructionGrantOperationError("Issued grant binding is invalid.")
        granted_at = self._iso(grant.granted_at)
        deadline = self._iso(grant.expires_at) if grant.expires_at is not None else None
        if deadline is not None and grant.expires_at <= grant.granted_at:
            raise ConstructionGrantOperationError("Issued grant expiration is invalid.")
        return dict(
            id=str(grant.id), user_id=str(target_user_id), role=role, scope_type=scope_type,
            scope_id=str(scope_id), status="active", expires_at=deadline, granted_at=granted_at,
        )

    async def grant_access(
        self, *, actor, target_user_id, role, scope_type, scope_id,
        expires_at=None, idempotency_key=None, trace_id=None,
    ):
        try:
            if (
                getattr(actor, "status", None) != "active"
                or not all(isinstance(value, UUID) for value in (
                    getattr(actor, "tenant_id", None), getattr(actor, "user_id", None), target_user_id, scope_id,
                ))
            ):
                raise ConstructionPermissionDeniedError("Construction access actor is not available.")
            if not self.session.in_transaction():
                raise ValueError("Grant issuance workflow requires an active transaction.")
            if idempotency_key is not None and (
                not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255
            ):
                raise ValueError("Idempotency key is invalid.")
            role = role.strip().upper() if isinstance(role, str) else ""
            scope_type = scope_type.strip().lower() if isinstance(scope_type, str) else ""
            require_construction_role_scope(role=role, scope_type=scope_type)
            if scope_type == "document":
                raise ConstructionScopeDeniedError("Document scope requires the Document Center validator.")
            if expires_at is not None:
                if not isinstance(expires_at, datetime) or expires_at.tzinfo is None or expires_at.utcoffset() is None:
                    raise ValueError("Construction access expiration must be aware.")
                expires_at = expires_at.astimezone(timezone.utc)
            target = dict(
                target_user_id=target_user_id, role=role, scope_type=scope_type,
                scope_id=scope_id, expires_at=expires_at,
            )
            await self.service._authorize(actor=actor, role=role, scope_type=scope_type, scope_id=scope_id)
            reservation = None
            if idempotency_key is not None:
                if self.idempotency is None:
                    raise ConstructionGrantOperationError("Grant idempotency is not configured.")
                reservation = await self.idempotency.reserve(
                    tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                    operation="construction.access_grant.create", idempotency_key=idempotency_key,
                    payload={
                        "target_user_id": str(target_user_id), "role": role, "scope_type": scope_type,
                        "scope_id": str(scope_id), "expires_at": expires_at.isoformat() if expires_at else None,
                    },
                    now=self.service._now(),
                )
                record = reservation.record
                if (
                    record.tenant_id != actor.tenant_id or record.principal_type != "user"
                    or record.principal_id != actor.user_id or record.operation != "construction.access_grant.create"
                ):
                    raise ConstructionGrantOperationError("Grant idempotency context is invalid.")
            if reservation is not None and reservation.is_replay:
                await self.service._authorize(actor=actor, role=role, scope_type=scope_type, scope_id=scope_id)
                if reservation.response_status is None:
                    raise ConstructionAccessIdempotencyInProgressError("Construction access operation is processing.")
                saved = reservation.response_payload
                if reservation.response_status != 201 or not isinstance(saved, dict) or set(saved) != PUBLIC_GRANT_FIELDS:
                    raise ConstructionGrantOperationError("Stored issuance response is invalid.")
                try:
                    grant_id = UUID(saved["id"])
                except (TypeError, ValueError, AttributeError):
                    raise ConstructionGrantOperationError("Stored issuance response is invalid.") from None
                grant = await self.service.repository.get_grant_for_update(
                    tenant_id=actor.tenant_id, grant_id=grant_id,
                )
                if grant is None or grant.id != grant_id:
                    raise ConstructionGrantOperationError("Issued grant is not available.")
                await self.service._authorize(actor=actor, role=role, scope_type=scope_type, scope_id=scope_id)
                response = self._public(grant, actor=actor, replay=True, **target)
                if saved != response:
                    raise ConstructionGrantOperationError("Stored issuance response is invalid.")
            else:
                grant = await self.service.grant_access(actor=actor, **target, trace_id=trace_id)
                response = self._public(grant, actor=actor, **target)
                if reservation is not None:
                    await self.idempotency.complete(
                        reservation=reservation, response_status=201,
                        response_payload=response, now=self.service._now(),
                    )
            await self.session.commit()
            return response
        except Exception as error:
            await self.session.rollback()
            if isinstance(error, (
                ConstructionGrantOperationError, ConstructionAccessGrantConflictError,
                ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
                ApiIdempotencyKeyReusedError, ValueError,
            )):
                raise
            if is_active_grant_overlap(error):
                raise ConstructionAccessGrantConflictError() from error
            raise ConstructionGrantOperationError("Construction access issuance workflow failed.") from None


def build_construction_access_issuance_workflow(*, session, idempotency_encryption_key=None, now_provider=None):
    from services.construction.construction_membership_writers import SerializedConstructionAccessIssuanceWorkflow
    return SerializedConstructionAccessIssuanceWorkflow(
        session=session,
        issuance_service=build_construction_access_issuance_service(session=session, now_provider=now_provider),
        idempotency_service=(
            build_api_idempotency_service(session, encryption_key=idempotency_encryption_key)
            if idempotency_encryption_key is not None else None
        ),
    )
