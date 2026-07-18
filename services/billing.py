import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from database.models import Invoice, PaidFeature, Payment, SpecialistPromotion
from database.repositories.billing import (
    BillingAccessError,
    BillingNotFoundError,
    BillingRepository,
    BillingValidationError,
)


class BillingError(Exception):
    pass


@dataclass(frozen=True)
class BillingInvoiceResult:
    invoice: Invoice
    promotion: SpecialistPromotion
    manual_instructions: str


@dataclass(frozen=True)
class BillingPaymentResult:
    payment: Payment
    status: str
    message: str


@dataclass(frozen=True)
class BillingMarkPaidResult:
    payment: Payment
    invoice: Invoice
    promotion: SpecialistPromotion | None
    approval_required: bool


class BillingService:
    def __init__(self, repository: BillingRepository):
        self.repository = repository

    async def list_paid_features(self, *, tenant_id: UUID) -> list[PaidFeature]:
        try:
            features = await self.repository.list_active_paid_features(tenant_id)
            await self.repository.session.commit()
            return features
        except Exception:
            await self.repository.session.rollback()
            raise

    async def create_manual_invoice(
        self,
        *,
        tenant_id: UUID,
        payer_user_id: UUID,
        feature_code: str,
        language: str,
    ) -> BillingInvoiceResult:
        normalized_feature_code = self._require_code(feature_code)

        try:
            specialist = await self.repository.get_approved_specialist_for_user(
                user_id=payer_user_id,
                tenant_id=tenant_id,
            )
            invoice, promotion = await self.repository.create_manual_invoice(
                tenant_id=tenant_id,
                payer_user_id=payer_user_id,
                specialist_id=specialist.id,
                feature_code=normalized_feature_code,
            )
            await self.repository.session.commit()
        except (BillingNotFoundError, BillingValidationError) as exc:
            await self.repository.session.rollback()
            raise BillingError(str(exc)) from exc
        except Exception:
            await self.repository.session.rollback()
            raise

        return BillingInvoiceResult(
            invoice=invoice,
            promotion=promotion,
            manual_instructions=self.get_manual_payment_instructions(language),
        )

    async def claim_manual_payment(
        self,
        *,
        tenant_id: UUID,
        payer_user_id: UUID,
        invoice_id: UUID,
    ) -> BillingPaymentResult:
        try:
            payment = await self.repository.claim_manual_payment(
                tenant_id=tenant_id,
                payer_user_id=payer_user_id,
                invoice_id=invoice_id,
            )
            await self.repository.session.commit()
        except (BillingNotFoundError, BillingValidationError) as exc:
            await self.repository.session.rollback()
            raise BillingError(str(exc)) from exc
        except Exception:
            await self.repository.session.rollback()
            raise

        return BillingPaymentResult(
            payment=payment,
            status=payment.status,
            message="Manual payment is pending review.",
        )

    async def list_pending_manual_payments(
        self,
        *,
        admin_user_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Payment]:
        try:
            return await self.repository.list_pending_manual_payments(
                admin_user_id=admin_user_id,
                limit=limit,
                offset=offset,
            )
        except BillingAccessError as exc:
            raise BillingError(str(exc)) from exc

    async def mark_payment_paid(
        self,
        *,
        admin_user_id: UUID,
        payment_id: UUID,
        reason: str,
    ) -> BillingMarkPaidResult:
        normalized_reason = self._require_reason(reason)
        threshold = self.get_manual_payment_approval_threshold_eur()

        try:
            payment, invoice, promotion, approval_required = (
                await self.repository.mark_payment_paid(
                    admin_user_id=admin_user_id,
                    payment_id=payment_id,
                    reason=normalized_reason,
                    approval_threshold_eur=threshold,
                )
            )
            await self.repository.session.commit()
        except (BillingAccessError, BillingNotFoundError, BillingValidationError) as exc:
            await self.repository.session.rollback()
            raise BillingError(str(exc)) from exc
        except Exception:
            await self.repository.session.rollback()
            raise

        return BillingMarkPaidResult(
            payment=payment,
            invoice=invoice,
            promotion=promotion,
            approval_required=approval_required,
        )

    async def expire_due_promotions(self) -> list[SpecialistPromotion]:
        try:
            promotions = await self.repository.expire_due_promotions()
            await self.repository.session.commit()
            return promotions
        except Exception:
            await self.repository.session.rollback()
            raise

    def get_manual_payment_instructions(self, language: str) -> str:
        normalized_language = language if language in {"ru", "en", "pt"} else "ru"
        env_key = f"MANUAL_PAYMENT_INSTRUCTIONS_{normalized_language.upper()}"

        value = (os.getenv(env_key) or "").strip()
        if value:
            return value

        fallback = (os.getenv("MANUAL_PAYMENT_INSTRUCTIONS_RU") or "").strip()
        if fallback:
            return fallback

        return {
            "ru": (
                "Реквизиты оплаты пока не настроены. "
                "Свяжитесь с администратором SGHR Beta."
            ),
            "en": (
                "Manual payment instructions are not configured yet. "
                "Please contact the SGHR Beta administrator."
            ),
            "pt": (
                "As instruções de pagamento manual ainda não estão configuradas. "
                "Entre em contato com o administrador do SGHR Beta."
            ),
        }[normalized_language]

    def get_manual_payment_approval_threshold_eur(self) -> Decimal:
        raw_value = (os.getenv("MANUAL_PAYMENT_APPROVAL_THRESHOLD_EUR") or "100").strip()

        try:
            value = Decimal(raw_value)
        except (InvalidOperation, ValueError):
            value = Decimal("100")

        if value < Decimal("0"):
            return Decimal("100")

        return value

    def _require_code(self, value: str | None) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise BillingError("Paid feature code is required.")
        return normalized

    def _require_reason(self, value: str | None) -> str:
        normalized = (value or "").strip()
        if len(normalized) < 3:
            raise BillingError("Reason is required.")
        return normalized[:500]