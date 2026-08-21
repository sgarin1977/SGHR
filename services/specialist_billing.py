from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import PaidFeature
from database.repositories.billing import (
    BillingRepository,
)
from database.repositories.translation import (
    TranslationRepository,
)
from services.billing import (
    BillingInvoiceResult,
    BillingPaymentResult,
    BillingService,
)
from services.translation import TranslationService
from services.user import UserService


class SpecialistBillingAccessError(
    PermissionError
):
    pass


@dataclass(frozen=True)
class SpecialistBillingActor:
    user_id: UUID
    tenant_id: UUID
    language: str


@dataclass(frozen=True)
class SpecialistBillingFeatures:
    actor: SpecialistBillingActor
    features: list[PaidFeature]


@dataclass(frozen=True)
class SpecialistBillingInvoiceAction:
    actor: SpecialistBillingActor
    result: BillingInvoiceResult


@dataclass(frozen=True)
class SpecialistBillingPaymentAction:
    actor: SpecialistBillingActor
    result: BillingPaymentResult


class SpecialistBillingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        users: UserService | None = None,
        translations: TranslationService | None = None,
        billing: BillingService | None = None,
    ):
        self.session = session
        self.users = users or UserService(session)
        self.translations = (
            translations
            or TranslationService(
                TranslationRepository(session)
            )
        )
        self.billing = billing or BillingService(
            BillingRepository(session)
        )

    @staticmethod
    def normalize_language(
        language: str | None,
    ) -> str:
        normalized = (
            language or "ru"
        ).strip().lower()

        if normalized == "ua":
            normalized = "uk"

        if normalized not in {
            "ru",
            "en",
            "pt",
            "uk",
            "pl",
            "de",
            "nl",
        }:
            return "ru"

        return normalized

    async def require_actor(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
    ) -> SpecialistBillingActor:
        user = await (
            self.users.get_user_by_telegram_id(
                platform_user_id
            )
        )

        if not user or user.tenant_id is None:
            raise SpecialistBillingAccessError(
                "Billing access denied."
            )

        language = await (
            self.translations
            .resolve_interface_language(
                user_id=user.id,
                fallback_language=(
                    user.language_code
                    or fallback_language
                ),
            )
        )

        return SpecialistBillingActor(
            user_id=user.id,
            tenant_id=user.tenant_id,
            language=self.normalize_language(
                language
            ),
        )

    async def open_panel(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
    ) -> SpecialistBillingActor:
        return await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

    async def list_paid_features(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
    ) -> SpecialistBillingFeatures:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        features = (
            await self.billing.list_paid_features(
                tenant_id=actor.tenant_id
            )
        )

        return SpecialistBillingFeatures(
            actor=actor,
            features=features,
        )

    async def create_manual_invoice(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        feature_code: str,
    ) -> SpecialistBillingInvoiceAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        result = (
            await self.billing.create_manual_invoice(
                tenant_id=actor.tenant_id,
                payer_user_id=actor.user_id,
                feature_code=feature_code,
                language=actor.language,
            )
        )

        return SpecialistBillingInvoiceAction(
            actor=actor,
            result=result,
        )

    async def claim_manual_payment(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        invoice_id: UUID,
    ) -> SpecialistBillingPaymentAction:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        result = (
            await self.billing.claim_manual_payment(
                tenant_id=actor.tenant_id,
                payer_user_id=actor.user_id,
                invoice_id=invoice_id,
            )
        )

        return SpecialistBillingPaymentAction(
            actor=actor,
            result=result,
        )

    async def record_unavailable_feature_opened(
        self,
        *,
        platform_user_id: int | str,
        fallback_language: str | None,
        feature: str,
        source: str,
    ) -> SpecialistBillingActor:
        actor = await self.require_actor(
            platform_user_id=platform_user_id,
            fallback_language=fallback_language,
        )

        await (
            self.billing
            .record_unavailable_feature_opened(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                feature=feature,
                source=source,
            )
        )

        return actor
