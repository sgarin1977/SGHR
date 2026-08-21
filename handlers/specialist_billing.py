import logging
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from database.models import Invoice, PaidFeature
from database.session import get_session
from handlers.start import (
    normalize_language,
    send_global_main_menu,
)
from services.billing import BillingError
from services.specialist_billing import (
    SpecialistBillingAccessError,
    SpecialistBillingService,
)
from ui.texts import t
from utils.telegram_cleanup import (
    edit_or_replace_menu_message,
)


specialist_billing_router = Router()
logger = logging.getLogger(__name__)


def billing_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("feature_disabled_beta", language),
                    callback_data="BETA_DISABLED:promotion",
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


def billing_status_label(
    status: str | None,
    language: str,
) -> str:
    labels = {
        "pending": {
            "ru": "Ожидает оплаты",
            "en": "Waiting for payment",
            "pt": "Aguardando pagamento",
            "uk": "Очікує оплати",
            "pl": "Oczekuje na płatność",
            "de": "Zahlung ausstehend",
            "nl": "In afwachting van betaling",
        },
        "claimed": {
            "ru": (
                "Оплата отправлена на проверку"
            ),
            "en": (
                "Payment sent for review"
            ),
            "pt": (
                "Pagamento enviado para revisão"
            ),
            "uk": (
                "Оплату надіслано на перевірку"
            ),
            "pl": (
                "Płatność wysłana do weryfikacji"
            ),
            "de": (
                "Zahlung zur Prüfung eingereicht"
            ),
            "nl": (
                "Betaling ter controle ingediend"
            ),
        },
        "paid": {
            "ru": "Оплачено",
            "en": "Paid",
            "pt": "Pago",
            "uk": "Оплачено",
            "pl": "Opłacono",
            "de": "Bezahlt",
            "nl": "Betaald",
        },
        "cancelled": {
            "ru": "Отменено",
            "en": "Cancelled",
            "pt": "Cancelado",
            "uk": "Скасовано",
            "pl": "Anulowano",
            "de": "Storniert",
            "nl": "Geannuleerd",
        },
        "failed": {
            "ru": "Не удалось оплатить",
            "en": "Payment failed",
            "pt": "Falha no pagamento",
            "uk": "Не вдалося оплатити",
            "pl": "Płatność nie powiodła się",
            "de": "Zahlung fehlgeschlagen",
            "nl": "Betaling mislukt",
        },
    }

    normalized_language = (
        language
        if language in {
            "ru",
            "en",
            "pt",
            "uk",
            "pl",
            "de",
            "nl",
        }
        else "ru"
    )

    return labels.get(
        status or "",
        {},
    ).get(
        normalized_language,
        "",
    )


def format_invoice_text(
    invoice: Invoice,
    manual_instructions: str,
    language: str,
) -> str:
    return (
        f"{t('billing_invoice_created', language)}\n\n"
        f"{t('billing_invoice_id', language)}: {invoice.id}\n"
        f"{t('billing_amount', language)}: {invoice.amount} {invoice.currency}\n"
        f"{t('admin_status', language)}: {billing_status_label(invoice.status, language)}\n\n"
        f"{t('billing_manual_instructions_title', language)}\n"
        f"{manual_instructions}"
    )


@specialist_billing_router.callback_query(F.data == "BILL_PANEL")
async def show_billing_panel(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            actor = await (
                SpecialistBillingService(
                    session
                ).open_panel(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                )
            )
    except SpecialistBillingAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    await callback.answer()
    await state.clear()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "billing_panel_title",
                actor.language,
            ),
            reply_markup=billing_menu_keyboard(
                actor.language
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@specialist_billing_router.callback_query(F.data == "BILL_MENU")
async def billing_to_menu(callback: CallbackQuery, state: FSMContext):
    await send_global_main_menu(callback, state)


@specialist_billing_router.callback_query(F.data == "BILL_FEATURES")
async def list_billing_features(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            action = await (
                SpecialistBillingService(
                    session
                ).list_paid_features(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                )
            )
    except SpecialistBillingAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return
    except BillingError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = action.actor.language
    features = action.features

    await state.update_data(
        billing_feature_codes=[
            feature.code
            for feature in features
        ],
    )
    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=format_features_text(
                features,
                language,
            ),
            reply_markup=(
                paid_features_keyboard(
                    features,
                    language,
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )


@specialist_billing_router.callback_query(F.data.startswith("BILL_BUY:"))
async def create_billing_invoice(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    feature_codes = (
        data.get("billing_feature_codes")
        or []
    )

    try:
        index = int(
            callback.data.split(":", 1)[1]
        )
    except (
        AttributeError,
        IndexError,
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    if (
        index < 0
        or index >= len(feature_codes)
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    feature_code = feature_codes[index]

    try:
        async with get_session() as session:
            action = await (
                SpecialistBillingService(
                    session
                ).create_manual_invoice(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    feature_code=feature_code,
                )
            )
    except SpecialistBillingAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return
    except BillingError as exc:
        logger.warning(
            "billing_invoice_create_failed "
            "telegram_id=%s "
            "feature_code=%s error=%s",
            callback.from_user.id,
            feature_code,
            exc,
        )
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = action.actor.language
    result = action.result

    logger.info(
        "billing_invoice_created "
        "telegram_id=%s user_id=%s "
        "invoice_id=%s feature_code=%s "
        "amount=%s currency=%s",
        callback.from_user.id,
        action.actor.user_id,
        result.invoice.id,
        feature_code,
        result.invoice.amount,
        result.invoice.currency,
    )

    await state.update_data(
        billing_invoice_id=str(
            result.invoice.id
        )
    )
    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=format_invoice_text(
                result.invoice,
                result.manual_instructions,
                language,
            ),
            reply_markup=invoice_keyboard(
                language
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@specialist_billing_router.callback_query(F.data == "BILL_CLAIM")
async def claim_billing_payment(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    invoice_id = data.get(
        "billing_invoice_id"
    )

    try:
        invoice_uuid = UUID(
            str(invoice_id)
        )
    except (
        TypeError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                fallback_language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            action = await (
                SpecialistBillingService(
                    session
                ).claim_manual_payment(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    invoice_id=invoice_uuid,
                )
            )
    except SpecialistBillingAccessError:
        await callback.answer(
            t(
                "billing_start_required",
                fallback_language,
            ),
            show_alert=True,
        )
        return
    except BillingError as exc:
        logger.warning(
            "billing_payment_claim_failed "
            "telegram_id=%s "
            "invoice_id=%s error=%s",
            callback.from_user.id,
            invoice_id,
            exc,
        )
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    language = action.actor.language
    result = action.result

    logger.info(
        "billing_payment_claimed "
        "telegram_id=%s user_id=%s "
        "invoice_id=%s payment_id=%s "
        "status=%s",
        callback.from_user.id,
        action.actor.user_id,
        invoice_uuid,
        result.payment.id,
        result.status,
    )

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "billing_payment_claimed",
                language,
            ).format(
                status=billing_status_label(
                    result.status,
                    language,
                ),
            ),
            reply_markup=billing_menu_keyboard(
                language
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )


@specialist_billing_router.callback_query(
    F.data.startswith("BETA_DISABLED:")
)
async def beta_disabled(
    callback: CallbackQuery,
    state: FSMContext,
):
    fallback_language = normalize_language(
        callback.from_user.language_code
    )
    feature = (
        (callback.data or "").split(
            ":",
            1,
        )[1]
        if ":" in (callback.data or "")
        else "unknown"
    )

    try:
        async with get_session() as session:
            actor = await (
                SpecialistBillingService(
                    session
                )
                .record_unavailable_feature_opened(
                    platform_user_id=(
                        callback.from_user.id
                    ),
                    fallback_language=(
                        fallback_language
                    ),
                    feature=feature,
                    source="specialist_cabinet",
                )
            )
        language = actor.language
    except SpecialistBillingAccessError:
        # Preserve the old behavior: an unknown
        # user still sees the disabled feature page.
        language = fallback_language

    await callback.answer()

    menu_message = (
        await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "feature_disabled_beta_message",
                language,
            ),
            reply_markup=(
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "billing_back",
                                    language,
                                ),
                                callback_data=(
                                    "M_CABINET"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=t(
                                    "search_menu",
                                    language,
                                ),
                                callback_data=(
                                    "BILL_MENU"
                                ),
                            )
                        ],
                    ]
                )
            ),
        )
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        )
    )
