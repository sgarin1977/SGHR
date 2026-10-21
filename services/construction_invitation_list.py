from datetime import datetime, timezone
from uuid import UUID

from services.construction_invitations import (
    ConstructionInvitationOperationError, ConstructionInvitationValidationError,
    resolve_construction_invitation_status,
)
from services.construction_permissions import (
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
    require_construction_access, require_construction_role_scope,
)

class ConstructionInvitationListService:
    def __init__(self, *, session, repository, now_provider=None):
        if (
            session is None or getattr(repository, "session", None) is not session
            or not callable(getattr(repository, "list_rows", None))
            or not callable(getattr(session, "in_transaction", None))
            or (now_provider is not None and not callable(now_provider))
        ):
            raise ValueError("Invitation list requires a Construction session.")
        self.session = session
        self.repository = repository
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def list_invitations(self, *, actor, access_context, project_id=None):
        if (
            getattr(actor, "status", None) != "active"
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
        ):
            raise ConstructionPermissionDeniedError("Invitation management is not available.")
        if project_id is not None and not isinstance(project_id, UUID):
            raise ConstructionInvitationValidationError("Project ID is invalid.")
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code="construction.team.manage", project_id=project_id,
        )
        try:
            if not self.session.in_transaction():
                raise ConstructionInvitationOperationError("Invitation transaction is not available.")
            as_of = self.now_provider()
            rows = await self.repository.list_rows(tenant_id=actor.tenant_id, project_id=project_id)
            invitations = {}
            fields = ("id", "tenant_id", "email", "role", "status", "expires_at", "grant_expires_at", "row_version")
            for row in rows:
                if row["tenant_id"] != actor.tenant_id or not isinstance(row["id"], UUID):
                    raise ConstructionInvitationOperationError("Invitation row is invalid.")
                record = {name: row[name] for name in fields}
                entry = invitations.setdefault(row["id"], {"record": record, "scopes": []})
                if entry["record"] != record:
                    raise ConstructionInvitationOperationError("Invitation snapshot is inconsistent.")
                entry["scopes"].append((row["scope_type"], row["scope_id"]))
            output = []
            for entry in invitations.values():
                record, scopes = entry["record"], entry["scopes"]
                if not scopes or len(set(scopes)) != len(scopes):
                    raise ConstructionInvitationOperationError("Invitation scopes are invalid.")
                for kind, target in scopes:
                    if (
                        kind not in ("tenant", "project") or not isinstance(target, UUID)
                        or (kind == "tenant" and target != actor.tenant_id)
                    ):
                        raise ConstructionInvitationOperationError("Invitation scope is invalid.")
                    try:
                        require_construction_role_scope(role=record["role"], scope_type=kind)
                    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
                        raise ConstructionInvitationOperationError("Invitation role or scope is invalid.") from None
                visible = True
                for kind, target in scopes:
                    try:
                        require_construction_access(
                            actor=actor, access_context=access_context,
                            permission_code="construction.team.manage",
                            project_id=target if kind == "project" else None,
                        )
                    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
                        visible = False
                        break
                if not visible:
                    continue
                if project_id is not None and not any(
                    kind == "tenant" or (kind == "project" and target == project_id)
                    for kind, target in scopes
                ):
                    continue
                if type(record["row_version"]) is not int or record["row_version"] <= 0:
                    raise ConstructionInvitationOperationError("Invitation version is invalid.")
                if not isinstance(record["email"], str) or not record["email"].strip():
                    raise ConstructionInvitationOperationError("Invitation email is invalid.")
                deadline = record["grant_expires_at"]
                if deadline is not None and (
                    not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None
                ):
                    raise ConstructionInvitationOperationError("Grant expiration is invalid.")
                status = resolve_construction_invitation_status(
                    status=record["status"], expires_at=record["expires_at"], as_of=as_of,
                )
                output.append({
                    "id": str(record["id"]), "email": record["email"], "role": record["role"],
                    "status": status,
                    "scopes": [{"scope_type": kind, "scope_id": str(target)} for kind, target in scopes],
                    "expires_at": record["expires_at"].isoformat(),
                    "grant_expires_at": deadline.isoformat() if deadline is not None else None,
                    "row_version": record["row_version"],
                })
            return output
        except (ConstructionInvitationOperationError, ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
            raise
        except Exception:
            raise ConstructionInvitationOperationError("Invitation list failed.") from None

def build_construction_invitation_list_service(*, session, now_provider=None):
    from database.repositories.construction_invitation_list import ConstructionInvitationListRepository
    return ConstructionInvitationListService(
        session=session, repository=ConstructionInvitationListRepository(session), now_provider=now_provider,
    )
