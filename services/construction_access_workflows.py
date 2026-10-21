from datetime import datetime, timezone
from uuid import UUID

from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction_access_mutations import (
    build_construction_access_mutation_service, is_active_grant_overlap,
)
from services.construction_grants import (
    ConstructionAccessContextResolver, ConstructionGrantOperationError,
)
from services.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_role_scope, require_delegable_construction_role,
    require_delegable_construction_scope,
)


PUBLIC_GRANT_FIELDS = frozenset({
    "id", "user_id", "role", "scope_type", "scope_id",
    "status", "expires_at", "granted_at",
})


class ConstructionAccessIdempotencyInProgressError(ConstructionGrantOperationError):
    status_code = 409
    code = "idempotency_in_progress"


class ConstructionAccessMutationWorkflow:
    """Current authorization; one commit for grant history, audit and optional replay."""

    def __init__(self, *, session, mutation_service, access_resolver, idempotency_service=None):
        collaborators = (
            mutation_service, getattr(mutation_service, "repository", None),
            getattr(mutation_service, "project_repository", None),
            getattr(getattr(mutation_service, "audit_logger", None), "event_repository", None),
            getattr(access_resolver, "repository", None),
        )
        if (
            session is None or any(getattr(item, "session", None) is not session for item in collaborators)
            or any(not callable(getattr(session, name, None)) for name in ("commit", "rollback", "in_transaction"))
            or not callable(getattr(access_resolver, "resolve", None))
            or any(not callable(getattr(mutation_service, name, None))
                   for name in ("replace_access", "revoke_access", "now_provider"))
            or (idempotency_service is not None and (
                getattr(getattr(idempotency_service, "repository", None), "session", None) is not session
                or any(not callable(getattr(idempotency_service, name, None)) for name in ("reserve", "complete"))
            ))
        ):
            raise ValueError("Grant workflow requires valid shared-session wiring.")
        self.session = session
        self.service = mutation_service
        self.resolver = access_resolver
        self.idempotency = idempotency_service

    def _now(self):
        instant = self.service.now_provider()
        if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Grant workflow requires an aware server clock.")
        return instant.astimezone(timezone.utc)

    async def revoke_access(self, *, actor, grant_id, idempotency_key=None, trace_id=None):
        return await self._run(
            actor=actor, grant_id=grant_id, target=None,
            idempotency_key=idempotency_key, trace_id=trace_id,
        )

    async def replace_access(
        self, *, actor, grant_id, role, scope_type, scope_id,
        expires_at=None, idempotency_key=None, trace_id=None,
    ):
        return await self._run(
            actor=actor, grant_id=grant_id,
            target=dict(role=role, scope_type=scope_type, scope_id=scope_id, expires_at=expires_at),
            idempotency_key=idempotency_key, trace_id=trace_id,
        )

    async def _run(self, *, actor, grant_id, target, idempotency_key, trace_id):
        try:
            if (
                getattr(actor, "status", None) != "active"
                or not all(isinstance(value, UUID) for value in (
                    getattr(actor, "user_id", None), getattr(actor, "tenant_id", None), grant_id,
                ))
            ):
                raise ConstructionPermissionDeniedError("Construction access actor is not available.")
            if not self.session.in_transaction():
                raise ValueError("Grant workflow requires an active transaction.")
            if idempotency_key is not None and (
                not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255
            ):
                raise ValueError("Idempotency key is invalid.")
            payload = {"grant_id": str(grant_id)}
            operation = "construction.access_grant.revoke"
            if target is not None:
                target = dict(target)
                target["role"] = target["role"].strip().upper() if isinstance(target["role"], str) else ""
                target["scope_type"] = (
                    target["scope_type"].strip().lower() if isinstance(target["scope_type"], str) else ""
                )
                if not isinstance(target["scope_id"], UUID):
                    raise ValueError("Construction scope must be a UUID.")
                require_construction_role_scope(role=target["role"], scope_type=target["scope_type"])
                if target["scope_type"] == "document":
                    raise ConstructionScopeDeniedError("Document scope requires the Document Center validator.")
                deadline = target["expires_at"]
                if deadline is not None:
                    if not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None:
                        raise ValueError("Construction access expiration must be aware.")
                    target["expires_at"] = deadline.astimezone(timezone.utc)
                payload.update(
                    role=target["role"], scope_type=target["scope_type"], scope_id=str(target["scope_id"]),
                    expires_at=target["expires_at"].isoformat() if target["expires_at"] else None,
                )
                operation = "construction.access_grant.replace"
            reservation = None
            if idempotency_key is not None:
                if self.idempotency is None:
                    raise ConstructionGrantOperationError("Grant idempotency is not configured.")
                reservation = await self.idempotency.reserve(
                    tenant_id=actor.tenant_id, principal_type="user", principal_id=actor.user_id,
                    operation=operation, idempotency_key=idempotency_key, payload=payload, now=self._now(),
                )
                record = reservation.record
                if (
                    record.tenant_id != actor.tenant_id or record.principal_type != "user"
                    or record.principal_id != actor.user_id or record.operation != operation
                ):
                    raise ConstructionGrantOperationError("Grant idempotency context is invalid.")
            original = await self.service.repository.get_grant_for_update(
                tenant_id=actor.tenant_id, grant_id=grant_id,
            )
            if original is None or original.id != grant_id or original.tenant_id != actor.tenant_id:
                raise ConstructionPermissionDeniedError("Construction access grant is not available.")
            if target is not None and target["scope_type"] == "project":
                project = await self.service.project_repository.get_active_project_for_update(
                    tenant_id=actor.tenant_id, project_id=target["scope_id"],
                )
                if (
                    project is None or project.id != target["scope_id"]
                    or project.tenant_id != actor.tenant_id or project.deleted_at is not None
                ):
                    raise ConstructionScopeDeniedError("Construction project is not available.")
            # Resolve after awaited reservation, original and target-project locks, including replay.
            access = await self.resolver.resolve(actor=actor)
            require_delegable_construction_scope(
                actor=actor, access_context=access, scope_type=original.scope_type, scope_id=original.scope_id,
            )
            # The original role must fit current authority even on replay.
            # Replacing OWNER with a lower role must not bypass its ceiling.
            require_delegable_construction_role(
                actor=actor, access_context=access, target_role=original.role,
                scope_type=original.scope_type, scope_id=original.scope_id,
            )
            if target is not None:
                require_delegable_construction_scope(
                    actor=actor, access_context=access,
                    scope_type=target["scope_type"], scope_id=target["scope_id"],
                )
                require_delegable_construction_role(
                    actor=actor, access_context=access, target_role=target["role"],
                    scope_type=target["scope_type"], scope_id=target["scope_id"],
                )
            if reservation is not None and reservation.is_replay:
                if reservation.response_status is None:
                    raise ConstructionAccessIdempotencyInProgressError("Construction access operation is processing.")
                response = reservation.response_payload
                if reservation.response_status != 200 or not isinstance(response, dict) or set(response) != PUBLIC_GRANT_FIELDS:
                    raise ConstructionGrantOperationError("Stored grant response is invalid.")
                expected_status = "active" if target is not None else "revoked"
                expected = target if target is not None else vars_for_scope(original)
                try:
                    saved_id = UUID(response["id"])
                    granted_at = datetime.fromisoformat(response["granted_at"])
                    deadline = datetime.fromisoformat(response["expires_at"]) if response["expires_at"] is not None else None
                    valid = (
                        response["id"] == str(saved_id) and response["user_id"] == str(original.user_id)
                        and response["status"] == expected_status
                        and response["role"] == expected["role"]
                        and response["scope_type"] == expected["scope_type"]
                        and response["scope_id"] == str(expected["scope_id"])
                        and response["expires_at"] == (
                            expected["expires_at"].astimezone(timezone.utc).isoformat()
                            if expected["expires_at"] is not None else None
                        )
                        and granted_at.tzinfo is not None and granted_at.utcoffset() is not None
                        and (deadline is None or (
                            deadline.tzinfo is not None and deadline.utcoffset() is not None and deadline > granted_at
                        ))
                        and (saved_id != grant_id if target is not None else saved_id == grant_id)
                        and original.status == "revoked"
                    )
                except (ValueError, TypeError, KeyError, AttributeError):
                    valid = False
                if not valid:
                    raise ConstructionGrantOperationError("Stored grant response is invalid.")
                response = dict(response)
            else:
                arguments = dict(actor=actor, access_context=access, grant_id=grant_id, trace_id=trace_id)
                if target is None:
                    result = await self.service.revoke_access(**arguments)
                else:
                    result = await self.service.replace_access(**arguments, **target)
                if (
                    result.tenant_id != actor.tenant_id or result.user_id != original.user_id
                    or result.status != ("active" if target is not None else "revoked")
                    or (result.id == grant_id if target is not None else result.id != grant_id)
                ):
                    raise ConstructionGrantOperationError("Grant mutation result is invalid.")
                response = {
                    "id": str(result.id), "user_id": str(result.user_id), "role": result.role,
                    "scope_type": result.scope_type, "scope_id": str(result.scope_id), "status": result.status,
                    "expires_at": result.expires_at.astimezone(timezone.utc).isoformat() if result.expires_at else None,
                    "granted_at": result.granted_at.astimezone(timezone.utc).isoformat(),
                }
                if reservation is not None:
                    await self.idempotency.complete(
                        reservation=reservation, response_status=200, response_payload=response, now=self._now(),
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
            raise ConstructionGrantOperationError("Construction access workflow failed.") from None


def vars_for_scope(grant):
    return dict(role=grant.role, scope_type=grant.scope_type, scope_id=grant.scope_id, expires_at=grant.expires_at)


def build_construction_access_mutation_workflow(*, session, idempotency_encryption_key=None):
    from services.api_idempotency import build_api_idempotency_service
    service = build_construction_access_mutation_service(session)
    resolver = ConstructionAccessContextResolver(repository=service.repository, now_provider=service.now_provider)
    from services.construction_membership_writers import SerializedConstructionAccessMutationWorkflow
    return SerializedConstructionAccessMutationWorkflow(
        session=session, mutation_service=service, access_resolver=resolver,
        idempotency_service=(
            build_api_idempotency_service(session, encryption_key=idempotency_encryption_key)
            if idempotency_encryption_key is not None else None
        ),
    )
