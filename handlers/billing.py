from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.models import Invoice, PaidFeature
from database.repositories.billing import BillingRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard, normalize_language
from services.billing import BillingError, BillingService
from services.user import UserService
from ui.texts import t


billing_router = Router()


async def get_billing_user_context(telegram_id: int | str):
    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return None, None
        return user.id, user.tenant_id


def billing_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("billing_promotions", language),
                    callback_data="BILL_FEATURES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="BILL_MENU",
                )
            ],
        ]
    )


def paid_features_keyboard(
    features: list[PaidFeature],
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, feature in enumerate(features):
        rows.append(
            [
                InlineKeyboardButton(
                    text=format_feature_button(feature),
                    callback_data=f"BILL_BUY:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("billing_back", language),
                callback_data="BILL_PANEL",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invoice_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("billing_i_paid", language),
                    callback_data="BILL_CLAIM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="BILL_FEATURES",
                )
            ],
        ]
    )


def format_feature_button(feature: PaidFeature) -> str:
    return f"{feature.name} - {feature.price} {feature.currency}"


def format_features_text(features: list[PaidFeature], language: str) -> str:
    if not features:
        return t("billing_no_features", language)

    lines = [t("billing_features_title", language), ""]
    for index, feature in enumerate(features, start=1):
        duration_days = (feature.extra_metadata or {}).get("duration_days")
        period = (
            t("billing_period_days", language).format(days=duration_days)
            if duration_days
            else t("billing_period_not_set", language)
        )
        lines.append(
            f"{index}. {feature.name}\n"
            f"{feature.description or ''}\n"
            f"{t('billing_price', language)}: {feature.price} {feature.currency}\n"
            f"{t('billing_period', language)}: {period}"
        )
        lines.append("")

    return "\n".join(lines).strip()


def format_invoice_text(
    invoice: Invoice,
    manual_instructions: str,
    language: str,
) -> str:
    return (
        f"{t('billing_invoice_created', language)}\n\n"
        f"{t('billing_invoice_id', language)}: {invoice.id}\n"
        f"{t('billing_amount', language)}: {invoice.amount} {invoice.currency}\n"
        f"{t('admin_status', language)}: {invoice.status}\n\n"
        f"{t('billing_manual_instructions_title', language)}\n"
        f"{manual_instructions}"
    )


@billing_router.callback_query(F.data == "M_CABINET")
async def billing_from_cabinet(callback: CallbackQuery, state: FSMContext):
    await show_billing_panel(callback, state)


@billing_router.callback_query(F.data == "BILL_PANEL")
async def show_billing_panel(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)

    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    await state.clear()
    await callback.message.answer(
        t("billing_panel_title", language),
        reply_markup=billing_menu_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "BILL_MENU")
async def billing_to_menu(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    await state.clear()
    await callback.message.answer(
        t("search_main_menu", language),
        reply_markup=get_main_menu_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "BILL_FEATURES")
async def list_billing_features(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)

    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            service = BillingService(BillingRepository(session))
            features = await service.list_paid_features(tenant_id=tenant_id)
    except BillingError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        billing_feature_codes=[feature.code for feature in features],
    )
    await callback.message.answer(
        format_features_text(features, language),
        reply_markup=paid_features_keyboard(features, language),
    )
    await callback.answer()


@billing_router.callback_query(F.data.startswith("BILL_BUY:"))
async def create_billing_invoice(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    feature_codes = data.get("billing_feature_codes") or []
    index = int(callback.data.split(":", 1)[1])

    if index < 0 or index >= len(feature_codes):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            service = BillingService(BillingRepository(session))
            result = await service.create_manual_invoice(
                tenant_id=tenant_id,
                payer_user_id=user_id,
                feature_code=feature_codes[index],
                language=language,
            )
    except BillingError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(billing_invoice_id=str(result.invoice.id))
    await callback.message.answer(
        format_invoice_text(result.invoice, result.manual_instructions, language),
        reply_markup=invoice_keyboard(language),
    )
    await callback.answer()


@billing_router.callback_query(F.data == "BILL_CLAIM")
async def claim_billing_payment(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    invoice_id = data.get("billing_invoice_id")

    if not invoice_id:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    user_id, tenant_id = await get_billing_user_context(callback.from_user.id)
    if not user_id or not tenant_id:
        await callback.answer(t("billing_start_required", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            result = await BillingService(
                BillingRepository(session)
            ).claim_manual_payment(
                tenant_id=tenant_id,
                payer_user_id=user_id,
                invoice_id=UUID(invoice_id),
            )
    except BillingError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        t("billing_payment_claimed", language).format(status=result.status),
        reply_markup=billing_menu_keyboard(language),
    )
    await callback.answer()