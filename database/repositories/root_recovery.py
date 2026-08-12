from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Country,
    Language,
    RoleScope,
    RootIdentity,
    RootRecoveryAction,
    RootRecoveryCode,
    RootRecoverySession,
    RootSecurityEvent,
    Tenant,
    User,
    UserRoleMapping,
)


class RootRecoveryRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def get_identity_by_name(
        self,
        identity_name: str,
        *,
        for_update: bool = False,
    ) -> RootIdentity | None:
        statement = (
            select(RootIdentity)
            .where(
                RootIdentity.identity_name.ilike(
                    identity_name.strip()
                )
            )
            .limit(1)
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def get_identity(
        self,
        identity_id: UUID,
        *,
        for_update: bool = False,
    ) -> RootIdentity | None:
        statement = (
            select(RootIdentity)
            .where(
                RootIdentity.id == identity_id
            )
            .limit(1)
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def create_identity(
        self,
        *,
        identity_name: str,
        password_hash: str,
        totp_secret_encrypted: str,
        linked_user_id: UUID | None,
    ) -> RootIdentity:
        identity = RootIdentity(
            identity_name=identity_name,
            linked_user_id=linked_user_id,
            password_hash=password_hash,
            totp_secret_encrypted=(
                totp_secret_encrypted
            ),
            status="pending_enrollment",
        )
        self.session.add(identity)
        await self.session.flush()
        return identity

    async def add_recovery_code_hashes(
        self,
        *,
        root_identity_id: UUID,
        code_hashes: list[str],
    ) -> None:
        for code_hash in code_hashes:
            self.session.add(
                RootRecoveryCode(
                    root_identity_id=(
                        root_identity_id
                    ),
                    code_hash=code_hash,
                )
            )

        await self.session.flush()

    async def list_available_recovery_codes(
        self,
        *,
        root_identity_id: UUID,
        for_update: bool = False,
    ) -> list[RootRecoveryCode]:
        statement = (
            select(RootRecoveryCode)
            .where(
                RootRecoveryCode.root_identity_id
                == root_identity_id,
                RootRecoveryCode.used_at.is_(None),
            )
            .order_by(
                RootRecoveryCode.created_at.asc()
            )
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def create_security_event(
        self,
        *,
        event_type: str,
        success: bool,
        root_identity_id: UUID | None = None,
        root_session_id: UUID | None = None,
        root_action_id: UUID | None = None,
        tenant_id: UUID | None = None,
        target_user_id: UUID | None = None,
        reason_code: str | None = None,
        payload: dict | None = None,
    ) -> RootSecurityEvent:
        event = RootSecurityEvent(
            root_identity_id=root_identity_id,
            root_session_id=root_session_id,
            root_action_id=root_action_id,
            tenant_id=tenant_id,
            target_user_id=target_user_id,
            event_type=event_type,
            success=success,
            reason_code=reason_code,
            payload=payload or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def tenant_exists(
        self,
        tenant_id: UUID,
    ) -> bool:
        result = await self.session.execute(
            select(Tenant.id)
            .where(Tenant.id == tenant_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def expire_stale_sessions(
        self,
        *,
        root_identity_id: UUID,
        now: datetime,
    ) -> list[RootRecoverySession]:
        result = await self.session.execute(
            select(RootRecoverySession)
            .where(
                RootRecoverySession.root_identity_id
                == root_identity_id,
                RootRecoverySession.state.in_(
                    {
                        "mfa_pending",
                        "reason_pending",
                        "active",
                    }
                ),
                RootRecoverySession.expires_at
                <= now,
            )
            .with_for_update()
        )
        sessions = list(result.scalars().all())

        for session in sessions:
            session.state = "expired"
            session.completed_at = now

        if sessions:
            await self.session.flush()

        return sessions

    async def get_open_session(
        self,
        *,
        root_identity_id: UUID,
        for_update: bool = False,
    ) -> RootRecoverySession | None:
        statement = (
            select(RootRecoverySession)
            .where(
                RootRecoverySession.root_identity_id
                == root_identity_id,
                RootRecoverySession.state.in_(
                    {
                        "mfa_pending",
                        "reason_pending",
                        "active",
                    }
                ),
            )
            .limit(1)
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def create_auth_session(
        self,
        *,
        root_identity_id: UUID,
        tenant_id: UUID,
        token_hash: str,
        password_verified_at: datetime,
        expires_at: datetime,
    ) -> RootRecoverySession:
        session = RootRecoverySession(
            root_identity_id=root_identity_id,
            tenant_id=tenant_id,
            token_hash=token_hash,
            state="mfa_pending",
            password_verified_at=(
                password_verified_at
            ),
            expires_at=expires_at,
            last_seen_at=password_verified_at,
        )
        self.session.add(session)
        await self.session.flush()
        return session

    async def get_session_by_token_hash(
        self,
        token_hash: str,
        *,
        for_update: bool = False,
    ) -> RootRecoverySession | None:
        statement = (
            select(RootRecoverySession)
            .where(
                RootRecoverySession.token_hash
                == token_hash
            )
            .limit(1)
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def mark_recovery_code_used(
        self,
        *,
        recovery_code: RootRecoveryCode,
        root_session_id: UUID,
        used_at: datetime,
    ) -> None:
        if recovery_code.used_at is not None:
            raise ValueError(
                "Recovery code is already used."
            )

        recovery_code.used_at = used_at
        recovery_code.used_by_session_id = (
            root_session_id
        )
        await self.session.flush()

    async def get_target_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> User | None:
        statement = (
            select(User)
            .where(
                User.id == user_id,
                User.tenant_id == tenant_id,
            )
            .limit(1)
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def expire_pending_actions(
        self,
        *,
        root_session_id: UUID,
        now: datetime,
    ) -> list[RootRecoveryAction]:
        result = await self.session.execute(
            select(RootRecoveryAction)
            .where(
                RootRecoveryAction.root_session_id
                == root_session_id,
                RootRecoveryAction.state
                == "pending_confirmation",
                RootRecoveryAction.expires_at
                <= now,
            )
            .with_for_update()
        )
        actions = list(result.scalars().all())

        for action in actions:
            action.state = "expired"

        if actions:
            await self.session.flush()

        return actions

    async def get_pending_action(
        self,
        *,
        root_session_id: UUID,
        for_update: bool = False,
    ) -> RootRecoveryAction | None:
        statement = (
            select(RootRecoveryAction)
            .where(
                RootRecoveryAction.root_session_id
                == root_session_id,
                RootRecoveryAction.state
                == "pending_confirmation",
            )
            .order_by(
                RootRecoveryAction.created_at.desc()
            )
            .limit(1)
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def create_pending_action(
        self,
        *,
        root_session_id: UUID,
        tenant_id: UUID,
        action_type: str,
        target_user_id: UUID,
        action_payload: dict,
        reason: str,
        confirmation_token_hash: str,
        expires_at: datetime,
    ) -> RootRecoveryAction:
        action = RootRecoveryAction(
            root_session_id=root_session_id,
            tenant_id=tenant_id,
            action_type=action_type,
            target_user_id=target_user_id,
            action_payload=action_payload,
            reason=reason,
            confirmation_token_hash=(
                confirmation_token_hash
            ),
            state="pending_confirmation",
            expires_at=expires_at,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def get_action(
        self,
        *,
        action_id: UUID,
        root_session_id: UUID,
        for_update: bool = False,
    ) -> RootRecoveryAction | None:
        statement = (
            select(RootRecoveryAction)
            .where(
                RootRecoveryAction.id == action_id,
                RootRecoveryAction.root_session_id
                == root_session_id,
            )
            .limit(1)
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def resolve_active_countries(
        self,
        country_codes: list[str],
    ) -> dict[str, Country]:
        if not country_codes:
            return {}

        result = await self.session.execute(
            select(Country).where(
                func.upper(Country.code).in_(
                    country_codes
                ),
                Country.is_active.is_(True),
            )
        )
        countries = list(result.scalars().all())

        return {
            country.code.upper(): country
            for country in countries
        }

    async def resolve_active_languages(
        self,
        language_codes: list[str],
    ) -> dict[str, Language]:
        if not language_codes:
            return {}

        result = await self.session.execute(
            select(Language).where(
                func.lower(Language.code).in_(
                    language_codes
                ),
                Language.is_active.is_(True),
            )
        )
        languages = list(result.scalars().all())

        return {
            language.code.lower(): language
            for language in languages
        }

    async def count_active_role_holders(
        self,
        *,
        tenant_id: UUID,
        role: str,
    ) -> int:
        result = await self.session.execute(
            select(
                func.count(
                    func.distinct(
                        UserRoleMapping.user_id
                    )
                )
            ).where(
                UserRoleMapping.tenant_id
                == tenant_id,
                UserRoleMapping.role == role,
                UserRoleMapping.status == "active",
            )
        )
        return int(result.scalar_one() or 0)

    async def get_latest_user_role(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: str,
        for_update: bool = False,
    ) -> UserRoleMapping | None:
        statement = (
            select(UserRoleMapping)
            .where(
                UserRoleMapping.tenant_id
                == tenant_id,
                UserRoleMapping.user_id
                == user_id,
                UserRoleMapping.role == role,
            )
            .order_by(
                UserRoleMapping.granted_at.desc()
            )
            .limit(1)
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return result.scalar_one_or_none()

    async def create_user_role(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: str,
        granted_at: datetime,
    ) -> UserRoleMapping:
        role_mapping = UserRoleMapping(
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            status="active",
            granted_by=None,
            granted_at=granted_at,
        )
        self.session.add(role_mapping)
        await self.session.flush()
        return role_mapping

    async def list_active_role_scopes(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: str,
        for_update: bool = False,
    ) -> list[RoleScope]:
        statement = (
            select(RoleScope)
            .where(
                RoleScope.tenant_id
                == tenant_id,
                RoleScope.user_id == user_id,
                RoleScope.role == role,
                RoleScope.status == "active",
            )
            .order_by(RoleScope.created_at.asc())
        )

        if for_update:
            statement = statement.with_for_update()

        result = await self.session.execute(
            statement
        )
        return list(result.scalars().all())

    async def create_country_scope(
        self,
        *,
        tenant_id: UUID,
        user_role_id: UUID,
        user_id: UUID,
        root_identity_id: UUID,
        country_id: UUID,
        reason: str,
        created_at: datetime,
    ) -> RoleScope:
        scope = RoleScope(
            tenant_id=tenant_id,
            user_role_id=user_role_id,
            user_id=user_id,
            role="admin",
            scope_type="country",
            scope_id=country_id,
            scope_code=None,
            status="active",
            reason=reason,
            created_by=None,
            created_by_root_identity_id=(
                root_identity_id
            ),
            created_at=created_at,
        )
        self.session.add(scope)
        await self.session.flush()
        return scope

    async def create_language_scope(
        self,
        *,
        tenant_id: UUID,
        user_role_id: UUID,
        user_id: UUID,
        root_identity_id: UUID,
        language_code: str,
        reason: str,
        created_at: datetime,
    ) -> RoleScope:
        scope = RoleScope(
            tenant_id=tenant_id,
            user_role_id=user_role_id,
            user_id=user_id,
            role="admin",
            scope_type="language",
            scope_id=None,
            scope_code=language_code,
            status="active",
            reason=reason,
            created_by=None,
            created_by_root_identity_id=(
                root_identity_id
            ),
            created_at=created_at,
        )
        self.session.add(scope)
        await self.session.flush()
        return scope
