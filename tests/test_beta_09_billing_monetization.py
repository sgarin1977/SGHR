from datetime import datetime, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import delete, or_, select

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
    LegalDocument,
    UserConsent,
)
from database.repositories.billing import BillingRepository
from services.billing import BillingError, BillingService
from tests.test_beta_04_specialist_registration import cleanup_test_user
from tests.test_beta_08_admin_moderation import (
    create_admin_user,
    create_pending_specialist,
    create_user_with_accepted_consents,
)


pytestmark = pytest.mark.asyncio
@pytest.fixture(autouse=True)
async def cleanup_test_legal_documents_after_billing_tests(db_session):
    yield

    await db_session.rollback()
    await db_session.execute(
        delete(UserConsent).where(
            UserConsent.version.like("test-beta-%"),
        )
    )
    await db_session.execute(
        delete(LegalDocument).where(
            LegalDocument.version.like("test-beta-%"),
        )
    )
    await db_session.commit()

async def cleanup_billing_for_specialist(session, specialist_id):
    await session.rollback()

    invoice_ids = list(
        (
            await session.execute(
                select(Invoice.id).where(Invoice.payer_entity_id == specialist_id)
            )
        )
        .scalars()
        .all()
    )

    payment_ids = []
    if invoice_ids:
        payment_ids = list(
            (
                await session.execute(
                    select(Payment.id).where(Payment.invoice_id.in_(invoice_ids))
                )
            )
            .scalars()
            .all()
        )

    target_ids = [specialist_id, *invoice_ids, *payment_ids]

    if target_ids:
        await session.execute(
            delete(AdminAction).where(AdminAction.target_id.in_(target_ids))
        )
        await session.execute(
            delete(EventLog).where(EventLog.entity_id.in_(target_ids))
        )
        await session.execute(
            delete(FinancialLedger).where(FinancialLedger.entity_id.in_(target_ids))
        )

    if invoice_ids:
        await session.execute(
            delete(ApprovalRequest).where(
                ApprovalRequest.action_type == "manual_payment_mark_paid"
            )
        )
        await session.execute(delete(Payment).where(Payment.invoice_id.in_(invoice_ids)))
        await session.execute(
            delete(SpecialistPromotion).where(
                SpecialistPromotion.invoice_id.in_(invoice_ids)
            )
        )
        await session.execute(
            delete(InvoiceItem).where(InvoiceItem.invoice_id.in_(invoice_ids))
        )
        await session.execute(delete(Invoice).where(Invoice.id.in_(invoice_ids)))

    await session.execute(
        delete(SpecialistPromotion).where(
            SpecialistPromotion.specialist_id == specialist_id,
            SpecialistPromotion.invoice_id.is_(None),
        )
    )
    await session.commit()


def test_beta_09_billing_static_contract():
    models_source = open("database/models.py", encoding="utf-8").read()
    repository_source = open("database/repositories/billing.py", encoding="utf-8").read()
    service_source = open("services/billing.py", encoding="utf-8").read()
    billing_handler_source = open("handlers/billing.py", encoding="utf-8").read()
    admin_source = open("handlers/admin.py", encoding="utf-8").read()
    bot_source = open("bot.py", encoding="utf-8").read()
    env_source = open(".env.example", encoding="utf-8").read()
    texts_source = open("ui/texts.py", encoding="utf-8").read()

    for fragment in [
        "class Plan",
        '__tablename__ = "plans"',
        "class PaidFeature",
        '__tablename__ = "paid_features"',
        "class SpecialistPromotion",
        '__tablename__ = "specialist_promotions"',
        "class Invoice",
        '__tablename__ = "invoices"',
        "class InvoiceItem",
        '__tablename__ = "invoice_items"',
        "class Payment",
        '__tablename__ = "payments"',
        "class FinancialLedger",
        '__tablename__ = "financial_ledger"',
    ]:
        assert fragment in models_source

    for fragment in [
        "DEFAULT_BETA_PAID_FEATURES",
        "specialist_premium",
        "top_in_category",
        "boost_profile",
        "featured_service",
        '"promotion_type": "premium"',
        '"promotion_type": "top_category"',
        '"promotion_type": "boost"',
        "ensure_default_paid_features",
        "create_manual_invoice",
        "claim_manual_payment",
        "list_pending_manual_payments",
        "mark_payment_paid",
        "expire_due_promotions",
        "manual_payment_mark_paid",
        "invoice_created",
        "manual_payment_paid",
    ]:
        assert fragment in repository_source

    for fragment in [
        "class BillingService",
        "create_manual_invoice",
        "claim_manual_payment",
        "list_pending_manual_payments",
        "mark_payment_paid",
        "get_manual_payment_instructions",
        "MANUAL_PAYMENT_APPROVAL_THRESHOLD_EUR",
    ]:
        assert fragment in service_source

    for fragment in [
        "billing_router = Router()",
        'F.data == "M_CABINET"',
        "BILL_FEATURES",
        "BILL_BUY:",
        "BILL_CLAIM",
        "billing_feature_codes",
        "billing_invoice_id",
    ]:
        assert fragment in billing_handler_source

    for fragment in [
        "ADM_PAYMENTS",
        "ADM_PAY_VIEW:",
        "ADM_PAY_PAID:",
        "admin_payment_ids",
        "entering_payment_paid_reason",
        "list_pending_manual_payments",
        "mark_payment_paid",
        "ADMIN_PAYMENT_MENU_ROLES",
        "finance_admin",
    ]:
        assert fragment in admin_source

    assert "from handlers.billing import billing_router" in bot_source
    assert "dp.include_router(billing_router)" in bot_source

    for fragment in [
        "MANUAL_PAYMENT_INSTRUCTIONS_RU",
        "MANUAL_PAYMENT_INSTRUCTIONS_EN",
        "MANUAL_PAYMENT_INSTRUCTIONS_PT",
        "MANUAL_PAYMENT_APPROVAL_THRESHOLD_EUR=100",
    ]:
        assert fragment in env_source

    for fragment in [
        "billing_panel_title",
        "billing_invoice_created",
        "billing_payment_claimed",
        "admin_pending_payments",
        "admin_payment_marked_paid",
        "admin_payment_approval_required",
    ]:
        assert fragment in texts_source

    assert 'callback_data=f"BILL_BUY:{index}"' in billing_handler_source
    assert 'callback_data=f"ADM_PAY_VIEW:{index - 1}"' in admin_source
    assert 'callback_data=f"ADM_PAY_PAID:{index}"' in admin_source


async def test_default_paid_features_are_created_and_listed(db_session):
    platform_user_id, user_id, tenant_id = await create_user_with_accepted_consents(
        db_session
    )

    try:
        service = BillingService(BillingRepository(db_session))
        features = await service.list_paid_features(tenant_id=tenant_id)

        codes = {feature.code for feature in features}
        assert {
            "specialist_premium",
            "top_in_category",
            "boost_profile",
            "featured_service",
        }.issubset(codes)

        assert "boost_7_days" not in codes
        assert "top_category_7_days" not in codes

        premium = next(feature for feature in features if feature.code == "specialist_premium")
        assert premium.price == Decimal("9.00")
        assert premium.currency == "EUR"
        assert premium.extra_metadata["promotion_type"] == "premium"
        assert premium.extra_metadata["duration_days"] == 30
    finally:
        await cleanup_test_user(db_session, platform_user_id)


async def test_create_manual_invoice_creates_invoice_item_promotion_and_ledger(db_session):
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )
    specialist.status = "active"
    await db_session.commit()
    specialist_id = specialist.id

    try:
        service = BillingService(BillingRepository(db_session))

        result = await service.create_manual_invoice(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            feature_code="specialist_premium",
            language="ru",
        )

        assert result.invoice.status == "issued"
        assert result.invoice.amount == Decimal("9.00")
        assert result.promotion.status == "pending_payment"
        assert result.promotion.promotion_type == "premium"
        assert result.promotion.invoice_id == result.invoice.id

        invoice_item = (
            await db_session.execute(
                select(InvoiceItem).where(InvoiceItem.invoice_id == result.invoice.id)
            )
        ).scalar_one_or_none()
        assert invoice_item is not None
        assert invoice_item.item_type == "paid_feature"
        assert invoice_item.amount == Decimal("9.00")

        ledger = (
            await db_session.execute(
                select(FinancialLedger).where(
                    FinancialLedger.entity_type == "invoice",
                    FinancialLedger.entity_id == result.invoice.id,
                    FinancialLedger.ledger_type == "invoice_created",
                )
            )
        ).scalar_one_or_none()
        assert ledger is not None
        assert ledger.direction == "debit"
    finally:
        await cleanup_billing_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_claim_manual_payment_creates_pending_payment(db_session):
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )
    specialist.status = "active"
    await db_session.commit()
    specialist_id = specialist.id

    try:
        service = BillingService(BillingRepository(db_session))

        invoice_result = await service.create_manual_invoice(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            feature_code="top_in_category",
            language="ru",
        )
        payment_result = await service.claim_manual_payment(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            invoice_id=invoice_result.invoice.id,
        )

        assert payment_result.status == "pending"
        assert payment_result.payment.payment_method == "manual"
        assert payment_result.payment.amount == Decimal("5.00")

        invoice = await db_session.get(Invoice, invoice_result.invoice.id)
        assert invoice.status == "disputed"
    finally:
        await cleanup_billing_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_admin_marks_payment_paid_and_activates_premium(db_session):
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )
    specialist.status = "active"
    await db_session.commit()
    specialist_id = specialist.id

    try:
        service = BillingService(BillingRepository(db_session))

        invoice_result = await service.create_manual_invoice(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            feature_code="specialist_premium",
            language="ru",
        )
        payment_result = await service.claim_manual_payment(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            invoice_id=invoice_result.invoice.id,
        )
        payment_id = payment_result.payment.id

        pending = await service.list_pending_manual_payments(admin_user_id=admin_user_id)
        assert any(item.id == payment_id for item in pending)

        paid_result = await service.mark_payment_paid(
            admin_user_id=admin_user_id,
            payment_id=payment_id,
            reason="manual payment received",
        )

        assert paid_result.approval_required is False
        assert paid_result.payment.status == "paid"
        assert paid_result.invoice.status == "paid"
        assert paid_result.promotion.status == "active"

        refreshed_specialist = await db_session.get(Specialist, specialist_id)
        assert refreshed_specialist.is_premium is True

        action = (
            await db_session.execute(
                select(AdminAction).where(
                    AdminAction.action_type == "mark_payment_paid",
                    AdminAction.target_id == payment_id,
                )
            )
        ).scalar_one_or_none()
        assert action is not None

        ledger = (
            await db_session.execute(
                select(FinancialLedger).where(
                    FinancialLedger.entity_type == "payment",
                    FinancialLedger.entity_id == payment_id,
                    FinancialLedger.ledger_type == "manual_payment_paid",
                )
            )
        ).scalar_one_or_none()
        assert ledger is not None
        assert ledger.direction == "credit"
    finally:
        await cleanup_billing_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)
        await cleanup_test_user(db_session, admin_platform_user_id)


async def test_finance_admin_can_mark_paid_but_moderator_cannot(db_session):
    finance_platform_user_id, finance_user_id, tenant_id = await create_admin_user(
        db_session,
        role="finance_admin",
    )
    moderator_platform_user_id, moderator_user_id, tenant_id = await create_admin_user(
        db_session,
        role="moderator",
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )
    specialist.status = "active"
    await db_session.commit()
    specialist_id = specialist.id

    try:
        service = BillingService(BillingRepository(db_session))

        invoice_result = await service.create_manual_invoice(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            feature_code="featured_service",
            language="ru",
        )
        payment_result = await service.claim_manual_payment(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            invoice_id=invoice_result.invoice.id,
        )
        payment_id = payment_result.payment.id

        with pytest.raises(BillingError):
            await service.mark_payment_paid(
                admin_user_id=moderator_user_id,
                payment_id=payment_id,
                reason="moderator should not confirm payment",
            )

        result = await service.mark_payment_paid(
            admin_user_id=finance_user_id,
            payment_id=payment_id,
            reason="finance checked manual payment",
        )

        assert result.payment.status == "paid"
        assert result.promotion.status == "active"
    finally:
        await cleanup_billing_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)
        await cleanup_test_user(db_session, moderator_platform_user_id)
        await cleanup_test_user(db_session, finance_platform_user_id)


async def test_boost_can_be_purchased_only_once_per_seven_days(db_session):
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )
    specialist.status = "active"
    await db_session.commit()
    specialist_id = specialist.id

    try:
        service = BillingService(BillingRepository(db_session))

        await service.create_manual_invoice(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            feature_code="boost_profile",
            language="ru",
        )

        with pytest.raises(BillingError):
            await service.create_manual_invoice(
                tenant_id=tenant_id,
                payer_user_id=specialist_user_id,
                feature_code="boost_profile",
                language="ru",
            )
    finally:
        await cleanup_billing_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)


async def test_payment_above_threshold_creates_approval_request(db_session, monkeypatch):
    admin_platform_user_id, admin_user_id, tenant_id = await create_admin_user(
        db_session,
        role="admin",
    )
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )
    specialist.status = "active"
    await db_session.commit()
    specialist_id = specialist.id

    try:
        service = BillingService(BillingRepository(db_session))

        invoice_result = await service.create_manual_invoice(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            feature_code="specialist_premium",
            language="ru",
        )
        invoice_id = invoice_result.invoice.id
        promotion_id = invoice_result.promotion.id

        invoice = await db_session.get(Invoice, invoice_id)
        promotion = await db_session.get(SpecialistPromotion, promotion_id)
        invoice.amount = Decimal("101.00")
        promotion.price = Decimal("101.00")
        await db_session.commit()

        payment_result = await service.claim_manual_payment(
            tenant_id=tenant_id,
            payer_user_id=specialist_user_id,
            invoice_id=invoice_id,
        )
        payment_id = payment_result.payment.id

        payment = await db_session.get(Payment, payment_id)
        payment.amount = Decimal("101.00")
        await db_session.commit()

        monkeypatch.setenv("MANUAL_PAYMENT_APPROVAL_THRESHOLD_EUR", "100")

        result = await service.mark_payment_paid(
            admin_user_id=admin_user_id,
            payment_id=payment_id,
            reason="large manual payment",
        )

        assert result.approval_required is True
        assert result.payment.status == "pending"
        assert result.invoice.status == "disputed"
        assert result.promotion.status == "pending_payment"

        approval = (
            await db_session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.action_type == "manual_payment_mark_paid",
                    ApprovalRequest.status == "pending",
                )
            )
        ).scalar_one_or_none()
        assert approval is not None
        assert str(payment_id) in approval.reason
    finally:
        await cleanup_billing_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)
        await cleanup_test_user(db_session, admin_platform_user_id)


async def test_expired_promotions_are_marked_expired(db_session):
    specialist_platform_user_id, specialist_user_id, tenant_id, specialist = (
        await create_pending_specialist(db_session)
    )
    specialist.status = "active"
    specialist.is_premium = True
    await db_session.commit()
    specialist_id = specialist.id

    try:
        promotion = SpecialistPromotion(
            tenant_id=tenant_id,
            specialist_id=specialist_id,
            promotion_type="premium",
            starts_at=datetime.utcnow() - timedelta(days=31),
            ends_at=datetime.utcnow() - timedelta(days=1),
            price=Decimal("9.00"),
            currency="EUR",
            invoice_id=None,
            status="active",
        )
        db_session.add(promotion)
        await db_session.commit()
        promotion_id = promotion.id

        service = BillingService(BillingRepository(db_session))
        expired = await service.expire_due_promotions()

        assert any(item.id == promotion_id for item in expired)

        refreshed_promotion = await db_session.get(SpecialistPromotion, promotion_id)
        refreshed_specialist = await db_session.get(Specialist, specialist_id)

        assert refreshed_promotion.status == "expired"
        assert refreshed_specialist.is_premium is False
    finally:
        await cleanup_billing_for_specialist(db_session, specialist_id)
        await cleanup_test_user(db_session, specialist_platform_user_id)