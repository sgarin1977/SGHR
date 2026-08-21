import logging
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.session import get_session
from handlers.admin_common import (
    AdminInterfaceLanguageMiddleware,
    normalize_admin_language,
    replace_admin_callback_screen,
    replace_admin_input_screen,
)
from services.admin_finance import (
    AdminFinanceAccessError,
    AdminFinanceService,
)
from services.billing import (
    BillingError,
    PendingManualPaymentCard,
)
from ui.texts import t


admin_finance_router = Router()
logger = logging.getLogger(__name__)

admin_finance_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_finance_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)


class AdminFinanceFSM(StatesGroup):
    entering_payment_paid_reason = State()


def admin_finance_exit_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "search_menu",
                        language,
                    ),
                    callback_data="ADM_MENU",
                )
            ]
        ]
    )


def pending_payment_keyboard(
    index: int,
    total: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "admin_mark_payment_paid",
                    language,
                ),
                callback_data=(
                    f"ADM_PAY_PAID:{index}"
                ),
            )
        ],
    ]

    navigation = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t(
                    "admin_prev",
                    language,
                ),
                callback_data=(
                    "ADM_PAY_VIEW:"
                    f"{index - 1}"
                ),
            )
        )

    if index + 1 < total:
        navigation.append(
            InlineKeyboardButton(
                text=t(
                    "admin_next",
                    language,
                ),
                callback_data=(
                    "ADM_PAY_VIEW:"
                    f"{index + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_panel_back",
                    language,
                ),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def format_pending_payment_card(
    card: PendingManualPaymentCard,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    invoice_status = (
        card.invoice_status
        or t(
            "admin_item_not_found",
            language,
        )
    )

    return (
        f"{t('admin_pending_payment_title', language).format(index=index + 1, total=total)}\n\n"
        f"{t('billing_invoice_id', language)}: "
        f"{card.invoice_id}\n"
        f"{t('billing_amount', language)}: "
        f"{card.amount} {card.currency}\n"
        f"{t('admin_status', language)}: "
        f"{card.payment_status}\n"
        f"{t('admin_invoice_status', language)}: "
        f"{invoice_status}\n"
        f"{t('billing_payment_method', language)}: "
        f"{card.payment_method}"
    )


@admin_finance_router.callback_query(
    F.data == "ADM_PAYMENTS"
)
async def list_pending_payments(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_admin_language(
        callback.from_user.language_code
    )
    await callback.answer(
        t(
            "feature_disabled_beta_message",
            language,
        ),
        show_alert=True,
    )


async def show_pending_payment(
    callback: CallbackQuery,
    state: FSMContext,
    index: int,
):
    data = await state.get_data()
    language = normalize_admin_language(
        callback.from_user.language_code
    )
    ids = data.get(
        "admin_payment_ids"
    ) or []

    if not ids:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_no_pending_payments",
                language,
            ),
            reply_markup=(
                admin_finance_exit_keyboard(
                    language
                )
            ),
        )
        return

    index = max(
        0,
        min(
            int(index),
            len(ids) - 1,
        ),
    )

    try:
        async with get_session() as session:
            card = await AdminFinanceService(
                session
            ).get_pending_payment_card(
                platform_user_id=(
                    callback.from_user.id
                ),
                payment_id=UUID(ids[index]),
            )
    except AdminFinanceAccessError:
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return
    except (BillingError, ValueError):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_pending_payment_card(
            card,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=(
            pending_payment_keyboard(
                index,
                len(ids),
                language,
            )
        ),
    )


@admin_finance_router.callback_query(
    F.data.startswith("ADM_PAY_VIEW:")
)
async def view_pending_payment(
    callback: CallbackQuery,
    state: FSMContext,
):
    try:
        index = int(
            callback.data.split(
                ":",
                1,
            )[1]
        )
    except (TypeError, ValueError):
        language = normalize_admin_language(
            callback.from_user.language_code
        )
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_payment_id=None,
        admin_payment_index=None,
    )

    await show_pending_payment(
        callback,
        state,
        index=index,
    )


@admin_finance_router.callback_query(
    F.data.startswith("ADM_PAY_PAID:")
)
async def ask_mark_payment_paid_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_admin_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            callback.data.split(
                ":",
                1,
            )[1]
        )
    except (TypeError, ValueError):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    ids = data.get(
        "admin_payment_ids"
    ) or []

    if index < 0 or index >= len(ids):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_payment_id=ids[index],
        admin_payment_index=index,
    )
    await state.set_state(
        AdminFinanceFSM
        .entering_payment_paid_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_reason_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data=(
                            "ADM_PAY_VIEW:"
                            f"{index}"
                        ),
                    )
                ]
            ]
        ),
    )


@admin_finance_router.message(
    AdminFinanceFSM
    .entering_payment_paid_reason
)
async def receive_mark_payment_paid_reason(
    message: Message,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_admin_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    payment_id = data.get(
        "admin_payment_id"
    )
    payment_index = int(
        data.get("admin_payment_index")
        or 0
    )

    cancel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "cancel",
                        language,
                    ),
                    callback_data=(
                        "ADM_PAY_VIEW:"
                        f"{payment_index}"
                    ),
                )
            ]
        ]
    )

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_reason_too_short",
                language,
            ),
            reply_markup=cancel_keyboard,
        )
        return

    if not payment_id:
        await state.set_state(None)
        await state.update_data(
            admin_payment_id=None,
            admin_payment_index=None,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=(
                admin_finance_exit_keyboard(
                    language
                )
            ),
        )
        return

    admin_user_id = None

    try:
        payment_uuid = UUID(payment_id)

        async with get_session() as session:
            action = await AdminFinanceService(
                session
            ).mark_payment_paid(
                platform_user_id=(
                    message.from_user.id
                ),
                payment_id=payment_uuid,
                reason=reason,
            )

        admin_user_id = (
            action.actor.user_id
        )
        result = action.result

        logger.info(
            "admin_payment_mark_paid "
            "telegram_id=%s "
            "admin_user_id=%s "
            "payment_id=%s "
            "approval_required=%s "
            "payment_status=%s",
            message.from_user.id,
            admin_user_id,
            payment_uuid,
            result.approval_required,
            result.payment.status,
        )
    except AdminFinanceAccessError:
        await state.set_state(None)
        await state.update_data(
            admin_payment_id=None,
            admin_payment_index=None,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=(
                admin_finance_exit_keyboard(
                    language
                )
            ),
        )
        return
    except (BillingError, ValueError) as exc:
        logger.warning(
            "admin_payment_mark_paid_failed "
            "telegram_id=%s "
            "admin_user_id=%s "
            "payment_id=%s "
            "error=%s",
            message.from_user.id,
            admin_user_id,
            payment_id,
            exc,
        )
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=str(exc),
            reply_markup=cancel_keyboard,
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_payment_id=None,
        admin_payment_index=None,
    )

    if result.approval_required:
        result_text = t(
            "admin_payment_approval_required",
            language,
        )
    else:
        result_text = t(
            "admin_payment_marked_paid",
            language,
        ).format(
            invoice_status=(
                result.invoice.status
            ),
            payment_status=(
                result.payment.status
            ),
            promotion_status=(
                result.promotion.status
                if result.promotion
                else "-"
            ),
        )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=result_text,
        reply_markup=(
            admin_finance_exit_keyboard(
                language
            )
        ),
    )
