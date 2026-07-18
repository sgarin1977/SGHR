from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AdminAction,
    ApprovalRequest,
    EventLog,
    FinancialLedger,
    Invoice,
    InvoiceItem,
    PaidFeature,
    Payment,
    Specialist,
    SpecialistPromotion,
    UserRoleMapping,
)


BILLING_ADMIN_ROLES = {"super_admin", "admin", "finance_admin"}
DEFAULT_BETA_PAID_FEATURES = [
    {
        "code": "specialist_premium",
        "name": "Specialist Premium",
        "description": "Premium profile for one month.",
        "price": Decimal("9.00"),
        "currency": "EUR",
        "promotion_type": "premium",
        "duration_days": 30,
        "sort_order": 10,
    },
    {
        "code": "top_in_category",
        "name": "Top in category",
        "description": "Higher category placement for seven days.",
        "price": Decimal("5.00"),
        "currency": "EUR",
        "promotion_type": "top_category",
        "duration_days": 7,
        "sort_order": 20,
    },
    {
        "code": "boost_profile",
        "name": "Boost profile",
        "description": "One profile boost for seven days.",
        "price": Decimal("2.00"),
        "currency": "EUR",
        "promotion_type": "boost",
        "duration_days": 7,
        "sort_order": 30,
    },
    {
        "code": "featured_service",
        "name": "Featured service",
        "description": "Featured service label for seven days.",
        "price": Decimal("3.00"),
        "currency": "EUR",
        "promotion_type": "featured_service",
        "duration_days": 7,
        "sort_order": 40,
    },
]


class BillingAccessError(Exception):
    pass


class BillingNotFoundError(Exception):
    pass


class BillingValidationError(Exception):
    pass


class BillingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_billing_admin_roles(self, user_id: UUID) -> set[str]:
        result = await self.session.execute(
            select(UserRoleMapping.role).where(
                UserRoleMapping.user_id == user_id,
                UserRoleMapping.status == "active",
                UserRoleMapping.role.in_(BILLING_ADMIN_ROLES),
            )
        )
        return set(result.scalars().all())

    async def require_billing_admin(self, user_id: UUID) -> set[str]:
        roles = await self.get_billing_admin_roles(user_id)
        if not roles:
            raise BillingAccessError("Billing admin access denied.")
        return roles

    async def ensure_default_paid_features(self, tenant_id: UUID) -> list[PaidFeature]:
        features: list[PaidFeature] = []

        for item in DEFAULT_BETA_PAID_FEATURES:
            existing = (
                await self.session.execute(
                    select(PaidFeature).where(
                        PaidFeature.tenant_id == tenant_id,
                        PaidFeature.code == item["code"],
                    )
                )
            ).scalar_one_or_none()

            metadata = {
                "promotion_type": item["promotion_type"],
                "duration_days": item["duration_days"],
                "sort_order": item["sort_order"],
                "is_default_beta_feature": True,
            }

            if existing:
                existing.name = item["name"]
                existing.description = item["description"]
                existing.price = item["price"]
                existing.currency = item["currency"]
                existing.status = "active"
                existing.extra_metadata = {
                    **(existing.extra_metadata or {}),
                    **metadata,
                }
                features.append(existing)
                continue

            feature = PaidFeature(
                tenant_id=tenant_id,
                code=item["code"],
                name=item["name"],
                description=item["description"],
                price=item["price"],
                currency=item["currency"],
                status="active",
                extra_metadata=metadata,
            )
            self.session.add(feature)
            features.append(feature)

        await self.session.flush()
        return features

    async def list_active_paid_features(self, tenant_id: UUID) -> list[PaidFeature]:
        await self.ensure_default_paid_features(tenant_id)

        result = await self.session.execute(
            select(PaidFeature).where(
                PaidFeature.tenant_id == tenant_id,
                PaidFeature.status == "active",
                PaidFeature.extra_metadata["is_default_beta_feature"].as_boolean().is_(True),
            )
        )
        features = list(result.scalars().all())
        return sorted(
            features,
            key=lambda item: int((item.extra_metadata or {}).get("sort_order", 999)),
        )

    async def get_paid_feature(self, tenant_id: UUID, feature_code: str) -> PaidFeature:
        await self.ensure_default_paid_features(tenant_id)

        feature = (
            await self.session.execute(
                select(PaidFeature).where(
                    PaidFeature.tenant_id == tenant_id,
                    PaidFeature.code == feature_code,
                    PaidFeature.status == "active",
                )
            )
        ).scalar_one_or_none()

        if not feature:
            raise BillingNotFoundError("Paid feature not found.")

        return feature

    async def get_approved_specialist_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Specialist:
        specialist = (
            await self.session.execute(
                select(Specialist).where(
                    Specialist.user_id == user_id,
                    Specialist.tenant_id == tenant_id,
                    Specialist.status == "approved",
                )
            )
        ).scalar_one_or_none()

        if not specialist:
            raise BillingNotFoundError(
                "Approved specialist profile not found."
            )

        return specialist
    async def create_manual_invoice(
        self,
        *,
        tenant_id: UUID,
        payer_user_id: UUID,
        specialist_id: UUID,
        feature_code: str,
    ) -> tuple[Invoice, SpecialistPromotion]:
        feature = await self.get_paid_feature(tenant_id, feature_code)
        promotion_type = self.get_feature_promotion_type(feature)
        duration_days = self.get_feature_duration_days(feature)

        if promotion_type == "boost":
            await self.ensure_boost_allowed(
                tenant_id=tenant_id,
                specialist_id=specialist_id,
            )

        now = datetime.utcnow()
        invoice = Invoice(
            tenant_id=tenant_id,
            payer_user_id=payer_user_id,
            payer_entity_type="specialist",
            payer_entity_id=specialist_id,
            amount=feature.price,
            currency=feature.currency,
            status="issued",
            issued_at=now,
            due_at=now + timedelta(days=3),
            extra_metadata={
                "feature_code": feature.code,
                "promotion_type": promotion_type,
                "duration_days": duration_days,
            },
        )
        self.session.add(invoice)
        await self.session.flush()

        invoice_item = InvoiceItem(
            invoice_id=invoice.id,
            item_type="paid_feature",
            description=feature.name,
            quantity=Decimal("1.00"),
            unit_price=feature.price,
            amount=feature.price,
            extra_metadata={
                "feature_code": feature.code,
                "promotion_type": promotion_type,
            },
        )
        self.session.add(invoice_item)

        promotion = SpecialistPromotion(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            promotion_type=promotion_type,
            starts_at=None,
            ends_at=None,
            price=feature.price,
            currency=feature.currency,
            invoice_id=invoice.id,
            status="pending_payment",
        )
        self.session.add(promotion)
        await self.session.flush()

        await self.create_ledger_entry(
            tenant_id=tenant_id,
            entity_type="invoice",
            entity_id=invoice.id,
            direction="debit",
            amount=invoice.amount,
            currency=invoice.currency,
            ledger_type="invoice_created",
            status="posted",
            metadata={
                "specialist_id": str(specialist_id),
                "promotion_id": str(promotion.id),
                "feature_code": feature.code,
            },
        )
        await self.log_event(
            tenant_id=tenant_id,
            user_id=payer_user_id,
            event_type="invoice_created",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={
                "specialist_id": str(specialist_id),
                "promotion_id": str(promotion.id),
                "feature_code": feature.code,
                "amount": str(invoice.amount),
                "currency": invoice.currency,
            },
        )
        await self.session.flush()
        return invoice, promotion

    async def claim_manual_payment(
        self,
        *,
        tenant_id: UUID,
        payer_user_id: UUID,
        invoice_id: UUID,
    ) -> Payment:
        invoice = await self.session.get(Invoice, invoice_id)
        if (
            not invoice
            or invoice.tenant_id != tenant_id
            or invoice.payer_user_id != payer_user_id
        ):
            raise BillingNotFoundError("Invoice not found.")

        if invoice.status not in {"issued", "disputed"}:
            raise BillingValidationError("Invoice cannot be marked as paid by user.")

        payment = (
            await self.session.execute(
                select(Payment).where(
                    Payment.invoice_id == invoice.id,
                    Payment.payment_method == "manual",
                    Payment.status.in_(["pending", "paid"]),
                )
            )
        ).scalar_one_or_none()

        if payment:
            return payment

        invoice.status = "disputed"

        payment = Payment(
            tenant_id=tenant_id,
            invoice_id=invoice.id,
            amount=invoice.amount,
            currency=invoice.currency,
            payment_method="manual",
            provider="manual",
            provider_payment_id=None,
            status="pending",
            extra_metadata={"claimed_by_user_id": str(payer_user_id)},
        )
        self.session.add(payment)

        await self.log_event(
            tenant_id=tenant_id,
            user_id=payer_user_id,
            event_type="manual_payment_claimed",
            entity_type="invoice",
            entity_id=invoice.id,
            payload={"payment_status": "pending"},
        )
        await self.session.flush()
        return payment

    async def list_pending_manual_payments(
        self,
        *,
        admin_user_id: UUID,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Payment]:
        await self.require_billing_admin(admin_user_id)

        result = await self.session.execute(
            select(Payment)
            .where(
                Payment.payment_method == "manual",
                Payment.status == "pending",
            )
            .order_by(Payment.created_at.asc())
            .offset(max(int(offset), 0))
            .limit(max(1, min(int(limit), 20)))
        )
        return list(result.scalars().all())

    async def mark_payment_paid(
        self,
        *,
        admin_user_id: UUID,
        payment_id: UUID,
        reason: str,
        approval_threshold_eur: Decimal,
    ) -> tuple[Payment, Invoice, SpecialistPromotion | None, bool]:
        await self.require_billing_admin(admin_user_id)

        payment = await self.session.get(Payment, payment_id)
        if not payment:
            raise BillingNotFoundError("Payment not found.")

        invoice = await self.session.get(Invoice, payment.invoice_id)
        if not invoice:
            raise BillingNotFoundError("Invoice not found.")

        promotion = (
            await self.session.execute(
                select(SpecialistPromotion).where(
                    SpecialistPromotion.invoice_id == invoice.id
                )
            )
        ).scalar_one_or_none()

        amount = Decimal(str(payment.amount))
        if payment.currency == "EUR" and amount > approval_threshold_eur:
            existing_request = (
                await self.session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.action_type == "manual_payment_mark_paid",
                        ApprovalRequest.status == "pending",
                        ApprovalRequest.reason.like(f"%{payment.id}%"),
                    )
                )
            ).scalar_one_or_none()

            if not existing_request:
                self.session.add(
                    ApprovalRequest(
                        tenant_id=payment.tenant_id,
                        action_type="manual_payment_mark_paid",
                        requested_by=admin_user_id,
                        status="pending",
                        reason=(
                            f"payment_id={payment.id}; invoice_id={invoice.id}; "
                            f"amount={payment.amount} {payment.currency}; reason={reason}"
                        ),
                    )
                )
                await self.session.flush()

            await self.log_admin_action(
                admin_user_id=admin_user_id,
                tenant_id=payment.tenant_id,
                action_type="manual_payment_approval_requested",
                target_type="payment",
                target_id=payment.id,
                before_state=self.payment_audit_state(payment, invoice, promotion),
                after_state=self.payment_audit_state(payment, invoice, promotion),
                reason=reason,
            )
            await self.session.flush()
            return payment, invoice, promotion, True

        before_state = self.payment_audit_state(payment, invoice, promotion)
        now = datetime.utcnow()

        payment.status = "paid"
        payment.paid_at = now
        invoice.status = "paid"
        invoice.paid_at = now

        if promotion:
            duration_days = int((invoice.extra_metadata or {}).get("duration_days", 7))
            promotion.status = "active"
            promotion.starts_at = now
            promotion.ends_at = now + timedelta(days=duration_days)

            specialist = await self.session.get(Specialist, promotion.specialist_id)
            if specialist:
                if promotion.promotion_type == "premium":
                    specialist.is_premium = True
                specialist.priority_score = self.calculate_priority_score(
                    specialist,
                    active_promotion_type=promotion.promotion_type,
                )
                specialist.updated_at = now

        await self.session.flush()

        await self.create_ledger_entry(
            tenant_id=payment.tenant_id,
            entity_type="payment",
            entity_id=payment.id,
            direction="credit",
            amount=payment.amount,
            currency=payment.currency,
            ledger_type="manual_payment_paid",
            status="posted",
            metadata={
                "invoice_id": str(invoice.id),
                "promotion_id": str(promotion.id) if promotion else None,
                "reason": reason,
            },
        )
        await self.log_admin_action(
            admin_user_id=admin_user_id,
            tenant_id=payment.tenant_id,
            action_type="mark_payment_paid",
            target_type="payment",
            target_id=payment.id,
            before_state=before_state,
            after_state=self.payment_audit_state(payment, invoice, promotion),
            reason=reason,
        )
        await self.log_event(
            tenant_id=payment.tenant_id,
            user_id=admin_user_id,
            event_type="manual_payment_paid",
            entity_type="payment",
            entity_id=payment.id,
            payload={
                "invoice_id": str(invoice.id),
                "promotion_id": str(promotion.id) if promotion else None,
                "reason": reason,
            },
        )
        await self.session.flush()
        return payment, invoice, promotion, False

    async def expire_due_promotions(self, *, now: datetime | None = None) -> list[SpecialistPromotion]:
        now = now or datetime.utcnow()
        result = await self.session.execute(
            select(SpecialistPromotion).where(
                SpecialistPromotion.status == "active",
                SpecialistPromotion.ends_at.isnot(None),
                SpecialistPromotion.ends_at <= now,
            )
        )
        promotions = list(result.scalars().all())

        touched_specialist_ids = set()
        for promotion in promotions:
            promotion.status = "expired"
            touched_specialist_ids.add(promotion.specialist_id)

        await self.session.flush()

        for specialist_id in touched_specialist_ids:
            await self.recalculate_specialist_promotions(specialist_id)

        await self.session.flush()
        return promotions

    async def recalculate_specialist_promotions(self, specialist_id: UUID) -> None:
        specialist = await self.session.get(Specialist, specialist_id)
        if not specialist:
            return

        now = datetime.utcnow()
        active_result = await self.session.execute(
            select(SpecialistPromotion).where(
                SpecialistPromotion.specialist_id == specialist_id,
                SpecialistPromotion.status == "active",
                or_(
                    SpecialistPromotion.ends_at.is_(None),
                    SpecialistPromotion.ends_at > now,
                ),
            )
        )
        active_types = {item.promotion_type for item in active_result.scalars().all()}

        specialist.is_premium = "premium" in active_types
        specialist.priority_score = self.calculate_priority_score(
            specialist,
            active_promotion_type=max(active_types) if active_types else None,
        )
        specialist.updated_at = now

    async def ensure_boost_allowed(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
    ) -> None:
        since = datetime.utcnow() - timedelta(days=7)
        existing = (
            await self.session.execute(
                select(SpecialistPromotion).where(
                    SpecialistPromotion.tenant_id == tenant_id,
                    SpecialistPromotion.specialist_id == specialist_id,
                    SpecialistPromotion.promotion_type == "boost",
                    SpecialistPromotion.created_at >= since,
                    SpecialistPromotion.status.in_(
                        ["pending_payment", "active"]
                    ),
                )
            )
        ).scalar_one_or_none()

        if existing:
            raise BillingValidationError("Boost profile can be purchased only once per 7 days.")

    async def create_ledger_entry(
        self,
        *,
        tenant_id: UUID,
        entity_type: str,
        entity_id: UUID,
        direction: str,
        amount,
        currency: str,
        ledger_type: str,
        status: str,
        metadata: dict,
    ) -> FinancialLedger:
        entry = FinancialLedger(
            tenant_id=tenant_id,
            entity_type=entity_type,
            entity_id=entity_id,
            direction=direction,
            amount=amount,
            currency=currency,
            ledger_type=ledger_type,
            status=status,
            extra_metadata=metadata,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def log_admin_action(
        self,
        *,
        admin_user_id: UUID,
        tenant_id: UUID,
        action_type: str,
        target_type: str,
        target_id: UUID,
        before_state: dict,
        after_state: dict,
        reason: str,
    ) -> AdminAction:
        action = AdminAction(
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
        )
        self.session.add(action)
        await self.session.flush()
        return action

    async def log_event(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        event_type: str,
        entity_type: str,
        entity_id: UUID,
        payload: dict,
    ) -> EventLog:
        event = EventLog(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            platform="telegram",
        )
        self.session.add(event)
        await self.session.flush()
        return event

    def get_feature_promotion_type(self, feature: PaidFeature) -> str:
        return str((feature.extra_metadata or {}).get("promotion_type") or feature.code)

    def get_feature_duration_days(self, feature: PaidFeature) -> int:
        return int((feature.extra_metadata or {}).get("duration_days") or 7)

    def calculate_priority_score(
        self,
        specialist: Specialist,
        *,
        active_promotion_type: str | None,
    ) -> Decimal:
        base = Decimal(str(specialist.priority_score or 0))
        promotion_bonus = {
            "top_category": Decimal("100.00"),
            "premium": Decimal("50.00"),
            "featured_service": Decimal("25.00"),
            "boost": Decimal("15.00"),
        }.get(active_promotion_type or "", Decimal("0.00"))

        return max(base, promotion_bonus)

    def payment_audit_state(
        self,
        payment: Payment,
        invoice: Invoice,
        promotion: SpecialistPromotion | None,
    ) -> dict:
        return {
            "payment_id": str(payment.id),
            "payment_status": payment.status,
            "invoice_id": str(invoice.id),
            "invoice_status": invoice.status,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "promotion_id": str(promotion.id) if promotion else None,
            "promotion_status": promotion.status if promotion else None,
            "promotion_type": promotion.promotion_type if promotion else None,
        }