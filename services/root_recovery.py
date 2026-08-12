from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from config import (
    ROOT_ACTION_TTL_SECONDS,
    ROOT_AUTH_COOLDOWN_SECONDS,
    ROOT_MFA_MAX_ATTEMPTS,
    ROOT_PASSWORD_MAX_ATTEMPTS,
    ROOT_PASSWORD_MIN_LENGTH,
    ROOT_RECOVERY_CODE_COUNT,
    ROOT_SECURITY_ENCRYPTION_KEY,
    ROOT_SESSION_TTL_SECONDS,
    ROOT_TOTP_ISSUER,
)
from database.models import RootRecoverySession
from database.role_policy import (
    ADMINISTRATIVE_ROLES,
)
from database.repositories.root_recovery import (
    RootRecoveryRepository,
)
from services.root_security import (
    RootSecurity,
    RootSecurityError,
)


class RootRecoveryError(RuntimeError):
    pass


class RootEnrollmentError(RootRecoveryError):
    pass


class RootAuthenticationError(RootRecoveryError):
    pass


class RootActionError(RootRecoveryError):
    pass


@dataclass(frozen=True)
class RootEnrollmentStart:
    identity_id: UUID
    identity_name: str
    qr_png: bytes


@dataclass(frozen=True)
class RootAuthenticationStart:
    session_id: UUID
    session_token: str
    expires_at: datetime


@dataclass(frozen=True)
class RootMfaResult:
    session_id: UUID
    state: str
    mfa_method: str


@dataclass(frozen=True)
class RootActiveSession:
    session_id: UUID
    root_identity_id: UUID
    tenant_id: UUID
    reason: str
    expires_at: datetime


ROOT_MANAGED_ADMIN_ROLES = (
    ADMINISTRATIVE_ROLES
)

ROOT_ACTION_TYPES = {
    "restore_super_admin",
    "grant_administrative_role",
    "revoke_administrative_role",
    "change_regional_admin_scopes",
}


@dataclass(frozen=True)
class RootActionRequest:
    action_id: UUID
    action_type: str
    target_user_id: UUID
    confirmation_token: str
    expires_at: datetime


def build_root_security() -> RootSecurity:
    return RootSecurity(
        encryption_key=(
            ROOT_SECURITY_ENCRYPTION_KEY or ""
        ),
        totp_issuer=ROOT_TOTP_ISSUER,
        password_min_length=(
            ROOT_PASSWORD_MIN_LENGTH
        ),
        recovery_code_count=(
            ROOT_RECOVERY_CODE_COUNT
        ),
    )


class RootRecoveryService:
    def __init__(
        self,
        repository: RootRecoveryRepository,
        security: RootSecurity,
    ):
        self.repository = repository
        self.security = security

    @staticmethod
    def normalize_identity_name(
        identity_name: str,
    ) -> str:
        normalized = (
            identity_name or ""
        ).strip()

        if len(normalized) < 3:
            raise RootEnrollmentError(
                "Root identity name is invalid."
            )

        return normalized

    async def start_enrollment(
        self,
        *,
        identity_name: str,
        password: str,
    ) -> RootEnrollmentStart:
        normalized_name = (
            self.normalize_identity_name(
                identity_name
            )
        )

        existing = (
            await self.repository
            .get_identity_by_name(
                normalized_name
            )
        )

        if existing:
            raise RootEnrollmentError(
                "Root identity already exists."
            )

        password_hash = (
            self.security.hash_password(password)
        )
        secret = (
            self.security.generate_totp_secret()
        )
        encrypted_secret = (
            self.security.encrypt_totp_secret(
                secret
            )
        )

        try:
            identity = (
                await self.repository.create_identity(
                    identity_name=normalized_name,
                    password_hash=password_hash,
                    totp_secret_encrypted=(
                        encrypted_secret
                    ),
                    linked_user_id=None,
                )
            )

            await self.repository.create_security_event(
                root_identity_id=identity.id,
                event_type=(
                    "root_enrollment_started"
                ),
                success=True,
                payload={
                    "identity_name": normalized_name,
                },
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        provisioning_uri = (
            self.security.build_totp_uri(
                identity_name=normalized_name,
                secret=secret,
            )
        )

        return RootEnrollmentStart(
            identity_id=identity.id,
            identity_name=normalized_name,
            qr_png=self.security.build_qr_png(
                provisioning_uri
            ),
        )

    async def confirm_enrollment(
        self,
        *,
        identity_name: str,
        totp_code: str,
    ) -> list[str]:
        normalized_name = (
            self.normalize_identity_name(
                identity_name
            )
        )

        identity = (
            await self.repository
            .get_identity_by_name(
                normalized_name,
                for_update=True,
            )
        )

        if not identity:
            raise RootEnrollmentError(
                "Root identity not found."
            )

        if identity.status != (
            "pending_enrollment"
        ):
            raise RootEnrollmentError(
                "Root enrollment is not pending."
            )

        if not identity.totp_secret_encrypted:
            await self._record_enrollment_failure(
                identity_id=identity.id,
                reason_code=(
                    "totp_secret_missing"
                ),
            )
            raise RootEnrollmentError(
                "Root TOTP is not configured."
            )

        try:
            secret = (
                self.security.decrypt_totp_secret(
                    identity
                    .totp_secret_encrypted
                )
            )
        except RootSecurityError:
            await self._record_enrollment_failure(
                identity_id=identity.id,
                reason_code=(
                    "totp_secret_unavailable"
                ),
            )
            raise RootEnrollmentError(
                "Root TOTP is unavailable."
            )

        if not self.security.verify_totp(
            secret,
            totp_code,
        ):
            await self._record_enrollment_failure(
                identity_id=identity.id,
                reason_code="invalid_totp",
            )
            raise RootEnrollmentError(
                "Invalid TOTP code."
            )

        recovery_codes = (
            self.security.generate_recovery_codes()
        )
        code_hashes = [
            self.security.hash_recovery_code(
                code
            )
            for code in recovery_codes
        ]
        now = datetime.now(timezone.utc)

        try:
            await (
                self.repository
                .add_recovery_code_hashes(
                    root_identity_id=identity.id,
                    code_hashes=code_hashes,
                )
            )

            identity.status = "active"
            identity.totp_confirmed_at = now
            identity.failed_password_attempts = 0
            identity.locked_until = None
            identity.updated_at = now

            await self.repository.create_security_event(
                root_identity_id=identity.id,
                event_type=(
                    "root_enrollment_confirmed"
                ),
                success=True,
                payload={
                    "recovery_codes_created": (
                        len(code_hashes)
                    ),
                },
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return recovery_codes

    async def _record_enrollment_failure(
        self,
        *,
        identity_id: UUID,
        reason_code: str,
    ) -> None:
        await self.repository.create_security_event(
            root_identity_id=identity_id,
            event_type=(
                "root_enrollment_confirmation"
            ),
            success=False,
            reason_code=reason_code,
        )
        await self.repository.session.commit()

    async def start_authentication(
        self,
        *,
        identity_name: str,
        password: str,
        tenant_id: UUID,
    ) -> RootAuthenticationStart:
        normalized_name = (
            self.normalize_identity_name(
                identity_name
            )
        )
        now = datetime.now(timezone.utc)

        if not await self.repository.tenant_exists(
            tenant_id
        ):
            await self.repository.create_security_event(
                event_type="root_password_authentication",
                success=False,
                reason_code="invalid_tenant",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Invalid Root credentials."
            )

        identity = await self.repository.get_identity_by_name(
            normalized_name,
            for_update=True,
        )

        if not identity:
            await self.repository.create_security_event(
                tenant_id=tenant_id,
                event_type="root_password_authentication",
                success=False,
                reason_code="invalid_credentials",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Invalid Root credentials."
            )

        expired_sessions = (
            await self.repository.expire_stale_sessions(
                root_identity_id=identity.id,
                now=now,
            )
        )

        for expired_session in expired_sessions:
            await self.repository.create_security_event(
                root_identity_id=identity.id,
                root_session_id=expired_session.id,
                tenant_id=expired_session.tenant_id,
                event_type="root_session_expired",
                success=True,
                reason_code="ttl_expired",
            )

        if (
            identity.status != "active"
            or not identity.totp_secret_encrypted
            or not identity.totp_confirmed_at
        ):
            await self.repository.create_security_event(
                root_identity_id=identity.id,
                tenant_id=tenant_id,
                event_type="root_password_authentication",
                success=False,
                reason_code="root_unavailable",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery is unavailable."
            )

        if (
            identity.locked_until
            and identity.locked_until > now
        ):
            await self.repository.create_security_event(
                root_identity_id=identity.id,
                tenant_id=tenant_id,
                event_type="root_password_authentication",
                success=False,
                reason_code="cooldown_active",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root authentication is temporarily locked."
            )

        open_session = (
            await self.repository.get_open_session(
                root_identity_id=identity.id,
                for_update=True,
            )
        )

        if open_session:
            await self.repository.create_security_event(
                root_identity_id=identity.id,
                root_session_id=open_session.id,
                tenant_id=open_session.tenant_id,
                event_type="root_password_authentication",
                success=False,
                reason_code="open_session_exists",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "A Root Recovery session is already open."
            )

        if not self.security.verify_password(
            identity.password_hash,
            password,
        ):
            identity.failed_password_attempts += 1
            locked = (
                identity.failed_password_attempts
                >= ROOT_PASSWORD_MAX_ATTEMPTS
            )

            if locked:
                identity.locked_until = (
                    now
                    + timedelta(
                        seconds=(
                            ROOT_AUTH_COOLDOWN_SECONDS
                        )
                    )
                )

            identity.updated_at = now

            await self.repository.create_security_event(
                root_identity_id=identity.id,
                tenant_id=tenant_id,
                event_type="root_password_authentication",
                success=False,
                reason_code=(
                    "password_attempts_exceeded"
                    if locked
                    else "invalid_credentials"
                ),
                payload={
                    "attempts": (
                        identity
                        .failed_password_attempts
                    ),
                },
            )
            await self.repository.session.commit()

            raise RootAuthenticationError(
                "Invalid Root credentials."
            )

        identity.failed_password_attempts = 0
        identity.locked_until = None
        identity.updated_at = now

        session_token = (
            self.security.generate_secure_token()
        )
        expires_at = (
            now
            + timedelta(
                seconds=ROOT_SESSION_TTL_SECONDS
            )
        )

        try:
            root_session = (
                await self.repository.create_auth_session(
                    root_identity_id=identity.id,
                    tenant_id=tenant_id,
                    token_hash=(
                        self.security.hash_token(
                            session_token
                        )
                    ),
                    password_verified_at=now,
                    expires_at=expires_at,
                )
            )

            await self.repository.create_security_event(
                root_identity_id=identity.id,
                root_session_id=root_session.id,
                tenant_id=tenant_id,
                event_type="root_password_authentication",
                success=True,
            )

            await self.repository.session.commit()

        except Exception:
            await self.repository.session.rollback()
            raise

        return RootAuthenticationStart(
            session_id=root_session.id,
            session_token=session_token,
            expires_at=expires_at,
        )

    async def verify_mfa(
        self,
        *,
        session_token: str,
        code: str,
        use_recovery_code: bool = False,
    ) -> RootMfaResult:
        now = datetime.now(timezone.utc)
        token_hash = self.security.hash_token(
            session_token
        )

        root_session = (
            await self.repository
            .get_session_by_token_hash(
                token_hash,
                for_update=True,
            )
        )

        if not root_session:
            await self.repository.create_security_event(
                event_type="root_mfa_authentication",
                success=False,
                reason_code="invalid_session",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Invalid Root Recovery session."
            )

        identity = await self.repository.get_identity(
            root_session.root_identity_id,
            for_update=True,
        )

        if not identity:
            await self.repository.create_security_event(
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_mfa_authentication",
                success=False,
                reason_code="identity_missing",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery is unavailable."
            )

        if root_session.expires_at <= now:
            root_session.state = "expired"
            root_session.completed_at = now

            await self.repository.create_security_event(
                root_identity_id=identity.id,
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_session_expired",
                success=True,
                reason_code="ttl_expired",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery session expired."
            )

        if root_session.state != "mfa_pending":
            await self.repository.create_security_event(
                root_identity_id=identity.id,
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_mfa_authentication",
                success=False,
                reason_code="invalid_session_state",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Invalid Root Recovery session."
            )

        if (
            identity.status != "active"
            or not identity.totp_secret_encrypted
            or not identity.totp_confirmed_at
        ):
            root_session.state = "locked"
            root_session.completed_at = now

            await self.repository.create_security_event(
                root_identity_id=identity.id,
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_mfa_authentication",
                success=False,
                reason_code="root_unavailable",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery is unavailable."
            )

        matched_recovery_code = None

        if use_recovery_code:
            mfa_method = "recovery_code"
            recovery_codes = await (
                self.repository
                .list_available_recovery_codes(
                    root_identity_id=identity.id,
                    for_update=True,
                )
            )

            for recovery_code in recovery_codes:
                if self.security.verify_recovery_code(
                    code_hash=recovery_code.code_hash,
                    code=code,
                ):
                    matched_recovery_code = recovery_code
                    break

            valid = matched_recovery_code is not None

        else:
            mfa_method = "totp"

            try:
                secret = (
                    self.security
                    .decrypt_totp_secret(
                        identity
                        .totp_secret_encrypted
                    )
                )
            except RootSecurityError:
                root_session.state = "locked"
                root_session.completed_at = now

                await self.repository.create_security_event(
                    root_identity_id=identity.id,
                    root_session_id=root_session.id,
                    tenant_id=root_session.tenant_id,
                    event_type="root_mfa_authentication",
                    success=False,
                    reason_code=(
                        "totp_secret_unavailable"
                    ),
                )
                await self.repository.session.commit()
                raise RootAuthenticationError(
                    "Root Recovery is unavailable."
                )

            valid = self.security.verify_totp(
                secret,
                code,
            )

        if not valid:
            root_session.mfa_attempts += 1
            locked = (
                root_session.mfa_attempts
                >= ROOT_MFA_MAX_ATTEMPTS
            )

            if locked:
                root_session.state = "locked"
                root_session.completed_at = now
                identity.locked_until = (
                    now
                    + timedelta(
                        seconds=(
                            ROOT_AUTH_COOLDOWN_SECONDS
                        )
                    )
                )

            identity.updated_at = now

            await self.repository.create_security_event(
                root_identity_id=identity.id,
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_mfa_authentication",
                success=False,
                reason_code=(
                    "mfa_attempts_exceeded"
                    if locked
                    else "invalid_mfa"
                ),
                payload={
                    "mfa_method": mfa_method,
                    "attempts": (
                        root_session.mfa_attempts
                    ),
                },
            )
            await self.repository.session.commit()

            raise RootAuthenticationError(
                "Invalid Root authentication code."
            )

        if matched_recovery_code:
            await self.repository.mark_recovery_code_used(
                recovery_code=matched_recovery_code,
                root_session_id=root_session.id,
                used_at=now,
            )

        root_session.state = "reason_pending"
        root_session.mfa_method = mfa_method
        root_session.mfa_verified_at = now
        root_session.last_seen_at = now

        identity.failed_password_attempts = 0
        identity.locked_until = None
        identity.last_authenticated_at = now
        identity.updated_at = now

        await self.repository.create_security_event(
            root_identity_id=identity.id,
            root_session_id=root_session.id,
            tenant_id=root_session.tenant_id,
            event_type="root_mfa_authentication",
            success=True,
            payload={
                "mfa_method": mfa_method,
            },
        )

        await self.repository.session.commit()

        return RootMfaResult(
            session_id=root_session.id,
            state=root_session.state,
            mfa_method=mfa_method,
        )

    async def activate_session(
        self,
        *,
        session_token: str,
        reason: str,
    ) -> RootActiveSession:
        now = datetime.now(timezone.utc)
        normalized_reason = (
            reason or ""
        ).strip()
        token_hash = self.security.hash_token(
            session_token
        )

        root_session = await (
            self.repository
            .get_session_by_token_hash(
                token_hash,
                for_update=True,
            )
        )

        if not root_session:
            await self.repository.create_security_event(
                event_type="root_session_activation",
                success=False,
                reason_code="invalid_session",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Invalid Root Recovery session."
            )

        if root_session.expires_at <= now:
            root_session.state = "expired"
            root_session.completed_at = now

            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_session_expired",
                success=True,
                reason_code="ttl_expired",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery session expired."
            )

        if root_session.state != "reason_pending":
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_session_activation",
                success=False,
                reason_code="invalid_session_state",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Invalid Root Recovery session."
            )

        if len(normalized_reason) < 3:
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_reason_saved",
                success=False,
                reason_code="reason_required",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery reason is required."
            )

        identity = await self.repository.get_identity(
            root_session.root_identity_id,
            for_update=True,
        )

        if (
            not identity
            or identity.status != "active"
            or not identity.totp_confirmed_at
        ):
            root_session.state = "locked"
            root_session.completed_at = now

            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_session_activation",
                success=False,
                reason_code="root_unavailable",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery is unavailable."
            )

        root_session.reason = normalized_reason
        root_session.state = "active"
        root_session.activated_at = now
        root_session.last_seen_at = now
        root_session.expires_at = (
            now
            + timedelta(
                seconds=ROOT_SESSION_TTL_SECONDS
            )
        )

        await self.repository.create_security_event(
            root_identity_id=identity.id,
            root_session_id=root_session.id,
            tenant_id=root_session.tenant_id,
            event_type="root_reason_saved",
            success=True,
        )
        await self.repository.create_security_event(
            root_identity_id=identity.id,
            root_session_id=root_session.id,
            tenant_id=root_session.tenant_id,
            event_type="root_session_activated",
            success=True,
        )
        await self.repository.session.commit()

        return RootActiveSession(
            session_id=root_session.id,
            root_identity_id=identity.id,
            tenant_id=root_session.tenant_id,
            reason=normalized_reason,
            expires_at=root_session.expires_at,
        )

    async def require_active_session(
        self,
        *,
        session_token: str,
        tenant_id: UUID | None = None,
    ) -> RootRecoverySession:
        now = datetime.now(timezone.utc)
        token_hash = self.security.hash_token(
            session_token
        )

        root_session = await (
            self.repository
            .get_session_by_token_hash(
                token_hash,
                for_update=True,
            )
        )

        if not root_session:
            raise RootAuthenticationError(
                "Active Root Recovery session required."
            )

        if root_session.expires_at <= now:
            root_session.state = "expired"
            root_session.completed_at = now

            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_session_expired",
                success=True,
                reason_code="ttl_expired",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery session expired."
            )

        if (
            root_session.state != "active"
            or (
                tenant_id is not None
                and root_session.tenant_id
                != tenant_id
            )
        ):
            raise RootAuthenticationError(
                "Active Root Recovery session required."
            )

        identity = await self.repository.get_identity(
            root_session.root_identity_id,
            for_update=True,
        )

        if (
            not identity
            or identity.status != "active"
            or not identity.totp_confirmed_at
        ):
            root_session.state = "locked"
            root_session.completed_at = now

            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_session_locked",
                success=False,
                reason_code="root_unavailable",
            )
            await self.repository.session.commit()
            raise RootAuthenticationError(
                "Root Recovery is unavailable."
            )

        root_session.last_seen_at = now
        await self.repository.session.flush()
        return root_session

    async def complete_session(
        self,
        *,
        session_token: str,
    ) -> None:
        root_session = await self.require_active_session(
            session_token=session_token
        )
        now = datetime.now(timezone.utc)

        root_session.state = "completed"
        root_session.completed_at = now
        root_session.last_seen_at = now

        await self.repository.create_security_event(
            root_identity_id=(
                root_session.root_identity_id
            ),
            root_session_id=root_session.id,
            tenant_id=root_session.tenant_id,
            event_type="root_session_completed",
            success=True,
        )
        await self.repository.session.commit()

    @staticmethod
    def normalize_action_payload(
        *,
        action_type: str,
        action_payload: dict | None,
    ) -> dict:
        payload = action_payload or {}

        if action_type == "restore_super_admin":
            if payload:
                raise ValueError(
                    "Restore Super Admin does not "
                    "accept a payload."
                )

            return {}

        if action_type in {
            "grant_administrative_role",
            "revoke_administrative_role",
        }:
            if set(payload) != {"role"}:
                raise ValueError(
                    "Administrative role is required."
                )

            raw_role = payload.get("role")

            if not isinstance(raw_role, str):
                raise ValueError(
                    "Administrative role is invalid."
                )

            role = raw_role.strip().lower()

            if role not in ROOT_MANAGED_ADMIN_ROLES:
                raise ValueError(
                    "Unsupported administrative role."
                )

            return {"role": role}

        if (
            action_type
            != "change_regional_admin_scopes"
        ):
            raise ValueError(
                "Unsupported Root action."
            )

        if set(payload) - {
            "country_codes",
            "language_codes",
        }:
            raise ValueError(
                "Unsupported Root action payload."
            )

        raw_country_codes = payload.get(
            "country_codes",
            [],
        )
        raw_language_codes = payload.get(
            "language_codes",
            [],
        )

        if not isinstance(
            raw_country_codes,
            list,
        ) or not isinstance(
            raw_language_codes,
            list,
        ):
            raise ValueError(
                "Scope codes must be lists."
            )

        if not all(
            isinstance(code, str)
            for code in (
                raw_country_codes
                + raw_language_codes
            )
        ):
            raise ValueError(
                "Scope codes must be strings."
            )

        country_codes = sorted(
            {
                code.strip().upper()
                for code in raw_country_codes
                if code.strip()
            }
        )
        language_codes = sorted(
            {
                code.strip().lower()
                for code in raw_language_codes
                if code.strip()
            }
        )

        if any(
            len(code) != 2
            for code in country_codes
        ):
            raise ValueError(
                "Country codes must contain "
                "two characters."
            )

        if any(
            not code or len(code) > 10
            for code in language_codes
        ):
            raise ValueError(
                "Language code is invalid."
            )

        # Empty lists intentionally revoke every active
        # regional scope while preserving the admin role.
        return {
            "country_codes": country_codes,
            "language_codes": language_codes,
        }

    async def request_action(
        self,
        *,
        session_token: str,
        action_type: str,
        target_user_id: UUID,
        reason: str,
        action_payload: dict | None = None,
    ) -> RootActionRequest:
        normalized_action_type = (
            action_type or ""
        ).strip().lower()
        normalized_reason = (
            reason or ""
        ).strip()
        root_session = (
            await self.require_active_session(
                session_token=session_token
            )
        )
        now = datetime.now(timezone.utc)

        try:
            if (
                normalized_action_type
                not in ROOT_ACTION_TYPES
            ):
                raise ValueError(
                    "Unsupported Root action."
                )

            if len(normalized_reason) < 3:
                raise ValueError(
                    "Root action reason is required."
                )

            normalized_payload = (
                self.normalize_action_payload(
                    action_type=(
                        normalized_action_type
                    ),
                    action_payload=action_payload,
                )
            )

        except ValueError as exc:
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                target_user_id=target_user_id,
                event_type="root_action_requested",
                success=False,
                reason_code="invalid_action_request",
                payload={
                    "action_type": (
                        normalized_action_type
                    ),
                },
            )
            await self.repository.session.commit()
            raise RootActionError(str(exc)) from exc

        target_user = await (
            self.repository.get_target_user(
                tenant_id=root_session.tenant_id,
                user_id=target_user_id,
                for_update=True,
            )
        )

        if (
            not target_user
            or target_user.status != "active"
        ):
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                target_user_id=target_user_id,
                event_type="root_action_requested",
                success=False,
                reason_code="target_not_found",
                payload={
                    "action_type": (
                        normalized_action_type
                    ),
                },
            )
            await self.repository.session.commit()
            raise RootActionError(
                "Target user not found."
            )

        expired_actions = await (
            self.repository.expire_pending_actions(
                root_session_id=root_session.id,
                now=now,
            )
        )

        for expired_action in expired_actions:
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                root_action_id=expired_action.id,
                tenant_id=root_session.tenant_id,
                target_user_id=(
                    expired_action.target_user_id
                ),
                event_type="root_action_expired",
                success=True,
                reason_code="ttl_expired",
                payload={
                    "action_type": (
                        expired_action.action_type
                    ),
                },
            )

        pending_action = await (
            self.repository.get_pending_action(
                root_session_id=root_session.id,
                for_update=True,
            )
        )

        if pending_action:
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                root_action_id=pending_action.id,
                tenant_id=root_session.tenant_id,
                target_user_id=(
                    pending_action.target_user_id
                ),
                event_type="root_action_requested",
                success=False,
                reason_code="pending_action_exists",
                payload={
                    "action_type": (
                        normalized_action_type
                    ),
                },
            )
            await self.repository.session.commit()
            raise RootActionError(
                "A Root action is already pending."
            )

        confirmation_token = (
            self.security.generate_secure_token()
        )
        expires_at = (
            now
            + timedelta(
                seconds=ROOT_ACTION_TTL_SECONDS
            )
        )

        action = await (
            self.repository.create_pending_action(
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                action_type=normalized_action_type,
                target_user_id=target_user_id,
                action_payload=normalized_payload,
                reason=normalized_reason,
                confirmation_token_hash=(
                    self.security.hash_token(
                        confirmation_token
                    )
                ),
                expires_at=expires_at,
            )
        )

        await self.repository.create_security_event(
            root_identity_id=(
                root_session.root_identity_id
            ),
            root_session_id=root_session.id,
            root_action_id=action.id,
            tenant_id=root_session.tenant_id,
            target_user_id=target_user_id,
            event_type="root_action_requested",
            success=True,
            payload={
                "action_type": (
                    normalized_action_type
                ),
                "scope": normalized_payload,
            },
        )
        await self.repository.session.commit()

        return RootActionRequest(
            action_id=action.id,
            action_type=action.action_type,
            target_user_id=action.target_user_id,
            confirmation_token=(
                confirmation_token
            ),
            expires_at=action.expires_at,
        )

    async def cancel_session(
        self,
        *,
        session_token: str,
    ) -> None:
        token_hash = self.security.hash_token(
            session_token
        )
        root_session = await (
            self.repository
            .get_session_by_token_hash(
                token_hash,
                for_update=True,
            )
        )

        if not root_session:
            raise RootAuthenticationError(
                "Root Recovery session not found."
            )

        if root_session.state not in {
            "mfa_pending",
            "reason_pending",
            "active",
        }:
            raise RootAuthenticationError(
                "Root Recovery session "
                "cannot be cancelled."
            )

        now = datetime.now(timezone.utc)
        root_session.state = "cancelled"
        root_session.completed_at = now
        root_session.last_seen_at = now

        await self.repository.create_security_event(
            root_identity_id=(
                root_session.root_identity_id
            ),
            root_session_id=root_session.id,
            tenant_id=root_session.tenant_id,
            event_type="root_session_cancelled",
            success=True,
        )
        await self.repository.session.commit()
