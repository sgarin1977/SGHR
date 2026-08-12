from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from database.repositories.root_recovery import (
    RootRecoveryRepository,
)
from services.root_recovery import (
    RootActionError,
    RootRecoveryService,
)
from services.root_role_actions import (
    RootRoleActionExecutor,
)
from services.root_security import RootSecurity


ACTION_EVENT_TYPES = {
    "restore_super_admin": (
        "root_super_admin_recovered"
    ),
    "grant_administrative_role": (
        "root_administrative_role_granted"
    ),
    "revoke_administrative_role": (
        "root_administrative_role_revoked"
    ),
    "change_regional_admin_scopes": (
        "root_regional_scopes_changed"
    ),
}


@dataclass(frozen=True)
class RootActionExecutionResult:
    action_id: UUID
    action_type: str
    target_user_id: UUID
    state: str
    result: dict


class RootActionService:
    def __init__(
        self,
        repository: RootRecoveryRepository,
        security: RootSecurity,
    ):
        self.repository = repository
        self.security = security
        self.recovery_service = (
            RootRecoveryService(
                repository,
                security,
            )
        )
        self.executor = RootRoleActionExecutor(
            repository
        )

    async def confirm_and_execute(
        self,
        *,
        session_token: str,
        action_id: UUID,
        confirmation_token: str,
    ) -> RootActionExecutionResult:
        root_session = await (
            self.recovery_service
            .require_active_session(
                session_token=session_token
            )
        )
        now = datetime.now(timezone.utc)

        action = await self.repository.get_action(
            action_id=action_id,
            root_session_id=root_session.id,
            for_update=True,
        )

        if not action:
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                tenant_id=root_session.tenant_id,
                event_type="root_action_confirmation",
                success=False,
                reason_code="action_not_found",
            )
            await self.repository.session.commit()
            raise RootActionError(
                "Root action not found."
            )

        if action.expires_at <= now:
            action.state = "expired"

            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                root_action_id=action.id,
                tenant_id=root_session.tenant_id,
                target_user_id=(
                    action.target_user_id
                ),
                event_type="root_action_expired",
                success=True,
                reason_code="ttl_expired",
                payload={
                    "action_type": (
                        action.action_type
                    ),
                },
            )
            await self.repository.session.commit()
            raise RootActionError(
                "Root action expired."
            )

        if action.state != "pending_confirmation":
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                root_action_id=action.id,
                tenant_id=root_session.tenant_id,
                target_user_id=(
                    action.target_user_id
                ),
                event_type="root_action_confirmation",
                success=False,
                reason_code="invalid_action_state",
                payload={
                    "action_type": (
                        action.action_type
                    ),
                },
            )
            await self.repository.session.commit()
            raise RootActionError(
                "Root action is not pending."
            )

        if not self.security.verify_token(
            token=confirmation_token,
            token_hash=(
                action.confirmation_token_hash
            ),
        ):
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                root_action_id=action.id,
                tenant_id=root_session.tenant_id,
                target_user_id=(
                    action.target_user_id
                ),
                event_type="root_action_confirmation",
                success=False,
                reason_code=(
                    "invalid_confirmation_token"
                ),
                payload={
                    "action_type": (
                        action.action_type
                    ),
                },
            )
            await self.repository.session.commit()
            raise RootActionError(
                "Invalid confirmation token."
            )

        action.state = "confirmed"
        action.confirmed_at = now

        await self.repository.create_security_event(
            root_identity_id=(
                root_session.root_identity_id
            ),
            root_session_id=root_session.id,
            root_action_id=action.id,
            tenant_id=root_session.tenant_id,
            target_user_id=action.target_user_id,
            event_type="root_action_confirmed",
            success=True,
            payload={
                "action_type": action.action_type,
            },
        )
        await self.repository.session.flush()

        try:
            async with (
                self.repository.session
                .begin_nested()
            ):
                execution_result = (
                    await self.executor.execute(
                        action=action,
                        root_identity_id=(
                            root_session
                            .root_identity_id
                        ),
                        now=now,
                    )
                )

        except RootActionError as exc:
            action.state = "failed"

            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                root_action_id=action.id,
                tenant_id=root_session.tenant_id,
                target_user_id=(
                    action.target_user_id
                ),
                event_type="root_action_execution",
                success=False,
                reason_code="execution_rejected",
                payload={
                    "action_type": (
                        action.action_type
                    ),
                },
            )
            await self.repository.session.commit()
            raise

        except Exception as exc:
            action.state = "failed"

            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                root_action_id=action.id,
                tenant_id=root_session.tenant_id,
                target_user_id=(
                    action.target_user_id
                ),
                event_type="root_action_execution",
                success=False,
                reason_code="execution_failed",
                payload={
                    "action_type": (
                        action.action_type
                    ),
                },
            )
            await self.repository.session.commit()
            raise RootActionError(
                "Root action execution failed."
            ) from exc

        action.state = "executed"
        action.executed_at = now

        event_type = ACTION_EVENT_TYPES.get(
            action.action_type,
            "root_action_executed",
        )

        await self.repository.create_security_event(
            root_identity_id=(
                root_session.root_identity_id
            ),
            root_session_id=root_session.id,
            root_action_id=action.id,
            tenant_id=root_session.tenant_id,
            target_user_id=action.target_user_id,
            event_type=event_type,
            success=True,
            payload={
                "action_type": action.action_type,
                "result": execution_result,
            },
        )
        await self.repository.session.commit()

        return RootActionExecutionResult(
            action_id=action.id,
            action_type=action.action_type,
            target_user_id=(
                action.target_user_id
            ),
            state=action.state,
            result=execution_result,
        )

    async def cancel_action(
        self,
        *,
        session_token: str,
        action_id: UUID,
    ) -> None:
        root_session = await (
            self.recovery_service
            .require_active_session(
                session_token=session_token
            )
        )

        action = await self.repository.get_action(
            action_id=action_id,
            root_session_id=root_session.id,
            for_update=True,
        )

        if (
            not action
            or action.state
            != "pending_confirmation"
        ):
            await self.repository.create_security_event(
                root_identity_id=(
                    root_session.root_identity_id
                ),
                root_session_id=root_session.id,
                root_action_id=(
                    action.id if action else None
                ),
                tenant_id=root_session.tenant_id,
                event_type="root_action_cancelled",
                success=False,
                reason_code="action_not_pending",
            )
            await self.repository.session.commit()
            raise RootActionError(
                "Pending Root action not found."
            )

        action.state = "cancelled"

        await self.repository.create_security_event(
            root_identity_id=(
                root_session.root_identity_id
            ),
            root_session_id=root_session.id,
            root_action_id=action.id,
            tenant_id=root_session.tenant_id,
            target_user_id=action.target_user_id,
            event_type="root_action_cancelled",
            success=True,
            payload={
                "action_type": action.action_type,
            },
        )
        await self.repository.session.commit()
