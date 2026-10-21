from datetime import datetime, timedelta


CONSTRUCTION_INVITATION_STATUSES = frozenset({
    "pending", "accepted", "declined", "expired", "revoked",
})
CONSTRUCTION_INVITATION_DEFAULT_TTL = timedelta(days=7)


class ConstructionInvitationValidationError(ValueError):
    pass


def resolve_construction_invitation_status(
    *, status: str, expires_at: datetime, as_of: datetime,
) -> str:
    if (
        not isinstance(status, str)
        or status not in CONSTRUCTION_INVITATION_STATUSES
    ):
        raise ConstructionInvitationValidationError(
            "Invitation status is invalid."
        )

    for timestamp in (expires_at, as_of):
        if (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise ConstructionInvitationValidationError(
                "Invitation timestamps must be timezone-aware."
            )

    if status == "pending" and expires_at <= as_of:
        return "expired"
    return status


def normalize_construction_invitation_email(value: str) -> str:
    from email_validator import EmailNotValidError, validate_email

    if not isinstance(value, str):
        raise ConstructionInvitationValidationError(
            "Invitation email is invalid."
        )

    normalized = value.strip().casefold()
    if not normalized or len(normalized) > 320:
        raise ConstructionInvitationValidationError(
            "Invitation email is invalid."
        )

    try:
        validate_email(normalized, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ConstructionInvitationValidationError(
            "Invitation email is invalid."
        ) from exc

    return normalized


class ConstructionInvitationRecipientDeniedError(Exception):
    pass


async def require_construction_invitation_email(
    *, actor, invitation_email, identity_reader,
) -> None:
    from uuid import UUID

    user_id = getattr(actor, "user_id", None)
    if (
        getattr(actor, "status", None) != "active"
        or not isinstance(user_id, UUID)
        or identity_reader is None
    ):
        raise ConstructionInvitationRecipientDeniedError(
            "Invitation recipient is not available."
        )

    normalized_email = normalize_construction_invitation_email(
        invitation_email
    )
    verified = await identity_reader.has_verified_email(
        user_id=user_id,
        email=normalized_email,
    )
    if verified is not True:
        raise ConstructionInvitationRecipientDeniedError(
            "Invitation recipient is not available."
        )


class ConstructionInvitationTransitionError(Exception):
    pass


def require_construction_invitation_transition(
    *, status: str, target_status: str,
    expires_at: datetime, as_of: datetime,
) -> None:
    effective_status = resolve_construction_invitation_status(
        status=status,
        expires_at=expires_at,
        as_of=as_of,
    )
    if (
        effective_status != "pending"
        or target_status not in ("accepted", "declined", "revoked")
    ):
        raise ConstructionInvitationTransitionError(
            "Invitation status transition is not allowed."
        )


def generate_construction_invitation_token() -> str:
    import secrets
    return secrets.token_urlsafe(32)


def hash_construction_invitation_token(token: str) -> str:
    import hashlib
    if not isinstance(token, str) or not token:
        raise ConstructionInvitationValidationError("Invitation token is invalid.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_construction_invitation_token(*, token, expected_hash) -> bool:
    import hmac
    if not isinstance(token, str) or not token:
        return False
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(char not in "0123456789abcdef" for char in expected_hash)
    ):
        return False
    return hmac.compare_digest(
        hash_construction_invitation_token(token), expected_hash,
    )


class ConstructionInvitationTokenDeniedError(Exception):
    pass


def require_construction_invitation_token(*, invitation, token, as_of) -> None:
    message = "Invitation token is not available."
    try:
        status = resolve_construction_invitation_status(
            status=getattr(invitation, "status", None),
            expires_at=getattr(invitation, "expires_at", None),
            as_of=as_of,
        )
    except ConstructionInvitationValidationError:
        raise ConstructionInvitationTokenDeniedError(message) from None
    if (
        status != "pending"
        or getattr(invitation, "token_used_at", True) is not None
        or not verify_construction_invitation_token(
            token=token, expected_hash=getattr(invitation, "token_hash", None),
        )
    ):
        raise ConstructionInvitationTokenDeniedError(message)


def require_construction_invitation_permissions(
    *, actor, access_context, role, scopes,
) -> None:
    from uuid import UUID
    from services.construction_permissions import (
        ConstructionPermissionDeniedError,
        ConstructionScopeDeniedError,
        require_construction_access,
        require_construction_role_scope,
        require_delegable_construction_role,
    )
    if (
        getattr(actor, "status", None) != "active"
        or not isinstance(getattr(actor, "user_id", None), UUID)
        or not isinstance(getattr(actor, "tenant_id", None), UUID)
    ):
        raise ConstructionPermissionDeniedError("Invitation actor is not available.")
    if not isinstance(scopes, (list, tuple)) or not scopes:
        raise ConstructionScopeDeniedError("Invitation scopes are not available.")
    for scope in scopes:
        if not isinstance(scope, (list, tuple)) or len(scope) != 2:
            raise ConstructionScopeDeniedError("Invitation scope is invalid.")
        scope_type, scope_id = scope
        if (
            scope_type not in ("tenant", "project")
            or not isinstance(scope_id, UUID)
            or (scope_type == "tenant" and scope_id != actor.tenant_id)
        ):
            raise ConstructionScopeDeniedError("Invitation scope is not available.")
        require_construction_role_scope(role=role, scope_type=scope_type)
        project_id = scope_id if scope_type == "project" else None
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code="construction.team.manage", project_id=project_id,
        )
        require_delegable_construction_role(
            actor=actor, access_context=access_context, target_role=role,
            scope_type=scope_type, scope_id=scope_id, project_id=project_id,
        )


async def require_construction_invitation_projects(
    *, actor, scopes, project_repository,
) -> None:
    from uuid import UUID
    from services.construction_permissions import ConstructionScopeDeniedError

    message = "Invitation project scope is not available."
    tenant_id = getattr(actor, "tenant_id", None)
    if (
        getattr(actor, "status", None) != "active"
        or not isinstance(tenant_id, UUID)
        or not isinstance(scopes, (list, tuple))
        or not scopes
    ):
        raise ConstructionScopeDeniedError(message)

    for scope in scopes:
        if not isinstance(scope, (list, tuple)) or len(scope) != 2:
            raise ConstructionScopeDeniedError(message)
        scope_type, scope_id = scope
        if not isinstance(scope_id, UUID):
            raise ConstructionScopeDeniedError(message)
        if scope_type == "tenant":
            if scope_id != tenant_id:
                raise ConstructionScopeDeniedError(message)
            continue
        if scope_type != "project" or project_repository is None:
            raise ConstructionScopeDeniedError(message)

        project = await project_repository.get_active_project(
            tenant_id=tenant_id,
            project_id=scope_id,
        )
        if (
            project is None
            or getattr(project, "id", None) != scope_id
            or getattr(project, "tenant_id", None) != tenant_id
            or getattr(project, "deleted_at", True) is not None
        ):
            raise ConstructionScopeDeniedError(message)


from dataclasses import dataclass, field


class ConstructionInvitationOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionInvitationCreationResult:
    invitation: object
    token: str = field(repr=False)


class ConstructionInvitationService:
    def __init__(
        self, *, session, repository, event_repository,
        project_repository=None, now_provider=None, token_provider=None,
    ):
        from datetime import timezone
        from services.construction_audit import ConstructionAuditLogger
        self.session = session
        self.repository = repository
        self.project_repository = project_repository
        self.audit_logger = ConstructionAuditLogger(event_repository=event_repository)
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.token_provider = token_provider or generate_construction_invitation_token

    async def create_invitation(
        self, *, actor, access_context, email, role, scopes,
        expires_at=None, grant_expires_at=None, trace_id=None,
    ):
        from services.construction_permissions import (
            ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
        )
        try:
            normalized_role = role.strip().upper() if isinstance(role, str) else ""
            require_construction_invitation_permissions(
                actor=actor, access_context=access_context,
                role=normalized_role, scopes=scopes,
            )
            normalized_scopes = list(dict.fromkeys(
                (kind, scope_id) for kind, scope_id in scopes
            ))
            normalized_email = normalize_construction_invitation_email(email)
            now = self.now_provider()
            if (
                not isinstance(now, datetime)
                or now.tzinfo is None or now.utcoffset() is None
            ):
                raise ConstructionInvitationValidationError("Invitation time is invalid.")
            invitation_expiry = expires_at if expires_at is not None else (
                now + CONSTRUCTION_INVITATION_DEFAULT_TTL
            )
            for deadline in (invitation_expiry, grant_expires_at):
                if deadline is not None and (
                    not isinstance(deadline, datetime)
                    or deadline.tzinfo is None or deadline.utcoffset() is None
                    or deadline <= now
                ):
                    raise ConstructionInvitationValidationError(
                        "Invitation and grant expiration must be future and timezone-aware."
                    )
            await require_construction_invitation_projects(
                actor=actor, scopes=normalized_scopes,
                project_repository=self.project_repository,
            )
            token = self.token_provider()
            token_hash = hash_construction_invitation_token(token)
            invitation = await self.repository.create_invitation(
                tenant_id=actor.tenant_id,
                email=normalized_email,
                user_id=None,
                role=normalized_role,
                scopes=normalized_scopes,
                expires_at=invitation_expiry,
                grant_expires_at=grant_expires_at,
                created_by_user_id=actor.user_id,
                token_hash=token_hash,
                delivery_metadata={"attempt_count": 0},
            )
            await self.audit_logger.record_operation(
                actor=actor,
                event_type="construction_invitation_created",
                entity_type="construction_invitation",
                entity_id=invitation.id,
                operation="create",
                details={
                    "role": normalized_role,
                    "scopes": [
                        {"scope_type": kind, "scope_id": str(scope_id)}
                        for kind, scope_id in normalized_scopes
                    ],
                    "expires_at": invitation_expiry.isoformat(),
                    "grant_expires_at": (
                        grant_expires_at.isoformat() if grant_expires_at else None
                    ),
                },
                trace_id=trace_id,
            )
            return ConstructionInvitationCreationResult(
                invitation=invitation, token=token,
            )
        except (
            ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError,
            ConstructionInvitationValidationError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionInvitationOperationError(
                "Construction invitation operation failed."
            ) from exc

    async def resend_invitation(
        self, *, actor, access_context, invitation_id,
        expected_row_version, expires_at=None, trace_id=None,
    ):
        from uuid import UUID
        from services.construction_audit import ConstructionAuditChange
        from services.construction_foundation import (
            ConstructionRowVersionConflictError,
            require_expected_row_version,
        )
        from services.construction_permissions import (
            ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError,
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
            if not isinstance(invitation_id, UUID):
                raise ConstructionInvitationValidationError(
                    "Invitation ID is invalid."
                )

            invitation = await self.repository.get_invitation_for_update(
                tenant_id=actor.tenant_id, invitation_id=invitation_id,
            )
            if (
                invitation is None
                or getattr(invitation, "id", None) != invitation_id
                or getattr(invitation, "tenant_id", None) != actor.tenant_id
            ):
                raise ConstructionScopeDeniedError("Invitation is not available.")

            rows = await self.repository.list_invitation_scopes(
                tenant_id=actor.tenant_id, invitation_id=invitation_id,
            )
            if not isinstance(rows, (list, tuple)) or any(
                getattr(row, "tenant_id", None) != actor.tenant_id
                or getattr(row, "invitation_id", None) != invitation_id
                for row in rows
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
            require_expected_row_version(
                actual_row_version=invitation.row_version,
                expected_row_version=expected_row_version,
            )

            now = self.now_provider()
            status = resolve_construction_invitation_status(
                status=invitation.status,
                expires_at=invitation.expires_at,
                as_of=now,
            )
            if (
                status != "pending"
                or getattr(invitation, "token_used_at", True) is not None
            ):
                raise ConstructionInvitationTransitionError(
                    "Invitation cannot be resent."
                )
            new_expiry = expires_at if expires_at is not None else (
                now + CONSTRUCTION_INVITATION_DEFAULT_TTL
            )
            if (
                not isinstance(new_expiry, datetime)
                or new_expiry.tzinfo is None
                or new_expiry.utcoffset() is None
                or new_expiry <= now
            ):
                raise ConstructionInvitationValidationError(
                    "Invitation expiration is invalid."
                )
            await require_construction_invitation_projects(
                actor=actor, scopes=scopes,
                project_repository=self.project_repository,
            )

            token = self.token_provider()
            token_hash = hash_construction_invitation_token(token)
            if token_hash == invitation.token_hash:
                raise ConstructionInvitationOperationError(
                    "Invitation token rotation failed."
                )
            old_expiry = invitation.expires_at
            invitation = await self.repository.resend_invitation(
                invitation=invitation,
                token_hash=token_hash,
                expires_at=new_expiry,
                delivery_metadata={"attempt_count": 0},
            )
            await self.audit_logger.record_operation(
                actor=actor,
                event_type="construction_invitation_resent",
                entity_type="construction_invitation",
                entity_id=invitation.id,
                operation="resend",
                changes=(ConstructionAuditChange(
                    field="expires_at",
                    old_value=old_expiry.isoformat(),
                    new_value=new_expiry.isoformat(),
                ),),
                trace_id=trace_id,
            )
            return ConstructionInvitationCreationResult(
                invitation=invitation, token=token,
            )
        except (
            ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError,
            ConstructionInvitationValidationError,
            ConstructionInvitationTransitionError,
            ConstructionRowVersionConflictError,
            ConstructionInvitationOperationError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionInvitationOperationError(
                "Invitation resend failed."
            ) from exc

    async def revoke_invitation(
        self, *, actor, access_context, invitation_id,
        expected_row_version, trace_id=None,
    ):
        from uuid import UUID
        from sqlalchemy.orm.exc import StaleDataError
        from services.construction_audit import ConstructionAuditChange
        from services.construction_foundation import (
            ConstructionRowVersionConflictError,
            require_expected_row_version,
        )
        from services.construction_permissions import (
            ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError,
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
            if not isinstance(invitation_id, UUID):
                raise ConstructionInvitationValidationError(
                    "Invitation ID is invalid."
                )

            invitation = await self.repository.get_invitation_for_update(
                tenant_id=actor.tenant_id, invitation_id=invitation_id,
            )
            if (
                invitation is None
                or getattr(invitation, "id", None) != invitation_id
                or getattr(invitation, "tenant_id", None) != actor.tenant_id
            ):
                raise ConstructionScopeDeniedError(
                    "Invitation is not available."
                )

            rows = await self.repository.list_invitation_scopes(
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
            require_expected_row_version(
                actual_row_version=invitation.row_version,
                expected_row_version=expected_row_version,
            )

            now = self.now_provider()
            require_construction_invitation_transition(
                status=invitation.status, target_status="revoked",
                expires_at=invitation.expires_at, as_of=now,
            )
            if getattr(invitation, "token_used_at", True) is not None:
                raise ConstructionInvitationTransitionError(
                    "Invitation cannot be revoked."
                )

            old_status = invitation.status
            invitation = await self.repository.revoke_invitation(
                invitation=invitation,
            )
            await self.audit_logger.record_operation(
                actor=actor,
                event_type="construction_invitation_revoked",
                entity_type="construction_invitation",
                entity_id=invitation.id,
                operation="revoke",
                changes=(ConstructionAuditChange(
                    field="status", old_value=old_status, new_value="revoked",
                ),),
                trace_id=trace_id,
            )
            return invitation
        except StaleDataError as exc:
            await self.session.rollback()
            raise ConstructionRowVersionConflictError() from exc
        except (
            ConstructionPermissionDeniedError,
            ConstructionScopeDeniedError,
            ConstructionInvitationValidationError,
            ConstructionInvitationTransitionError,
            ConstructionRowVersionConflictError,
            ConstructionInvitationOperationError,
        ):
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionInvitationOperationError(
                "Invitation revoke failed."
            ) from exc



def build_construction_invitation_identity_reader(*, session):
    """Build the existing canonical Platform email reader without writes."""
    from database.repositories.user import UserRepository

    return UserRepository(session)
