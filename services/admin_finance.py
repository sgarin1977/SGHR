from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment
from database.repositories.billing import (
    BillingRepository,
)
from services.billing import (
    BillingMarkPaidResult,
    BillingService,
    PendingManualPaymentCard,
)
from services.user import UserService


class AdminFinanceAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminFinanceActor:
    user_id: UUID
    tenant_id: UUID


@dataclass(frozen=True)
class AdminFinanceMarkPaidAction:
    actor: AdminFinanceActor
    result: BillingMarkPaidResult


class AdminFinanceService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        billing: BillingService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.billing = billing or BillingService(
            BillingRepository(session)
        )

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
    ) -> AdminFinanceActor:
        user = (
            await self.users
            .get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise AdminFinanceAccessError(
                "Administrative financial "
                "access denied."
            )

        return AdminFinanceActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
        )

    async def list_pending_payments(
        self,
        *,
        platform_user_id: int | str,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Payment]:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.billing
            .list_pending_manual_payments(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                limit=limit,
                offset=offset,
            )
        )

    async def get_pending_payment_card(
        self,
        *,
        platform_user_id: int | str,
        payment_id: UUID,
    ) -> PendingManualPaymentCard:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        return await (
            self.billing
            .get_pending_manual_payment_card(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                payment_id=payment_id,
            )
        )

    async def mark_payment_paid(
        self,
        *,
        platform_user_id: int | str,
        payment_id: UUID,
        reason: str,
    ) -> AdminFinanceMarkPaidAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id
        )

        result = await (
            self.billing.mark_payment_paid(
                admin_user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                payment_id=payment_id,
                reason=reason,
            )
        )

        return AdminFinanceMarkPaidAction(
            actor=actor,
            result=result,
        )
