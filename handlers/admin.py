import logging
from uuid import UUID
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import AdminAction, Complaint, EventLog, Invoice, Payment, Review, Specialist
from database.repositories.moderation import ModerationRepository
from database.repositories.billing import BillingRepository
from database.repositories.reviews import ReviewRepository
from database.repositories.portfolio import PortfolioRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard, normalize_language
from services.moderation import ModerationError, ModerationService
from services.billing import BillingError, BillingService
from services.reviews import ReviewService, ReviewServiceError
from services.user import UserService
from services.portfolio import PortfolioService, PortfolioServiceError
from ui.texts import t

admin_router = Router()
logger = logging.getLogger(__name__)
ADMIN_MODERATION_MENU_ROLES = {"super_admin", "admin", "moderator"}
ADMIN_PAYMENT_MENU_ROLES = {"super_admin", "admin", "finance_admin"}
ADMIN_ROLE_MENU_ROLES = {"super_admin"}
ADMIN_LOG_MENU_ROLES = {"super_admin", "admin", "support"}
class AdminModerationFSM(StatesGroup):
    entering_reject_reason = State()
    entering_complaint_resolution_reason = State()
    entering_block_reason = State()
    entering_payment_paid_reason = State()
    entering_role_grant = State()
    entering_role_revoke = State()
    entering_review_reject_reason = State()
    entering_review_hide_reason = State()


async def get_admin_user_context(telegram_id: int | str):
    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return None, None, set()

        service = ModerationService(ModerationRepository(session))
        roles = await service.get_admin_roles(user.id)
        return user.id, user.tenant_id, roles


def admin_panel_keyboard(language: str, roles: set[str] | None = None) -> InlineKeyboardMarkup:
    roles = roles or set()
    rows = []

    if roles.intersection(ADMIN_MODERATION_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_pending_profiles", language),
                    callback_data="ADM_PENDING",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_open_complaints", language),
                    callback_data="ADM_COMPLAINTS",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_pending_reviews", language),
                    callback_data="ADM_REVIEWS",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_pending_portfolio", language),
                    callback_data="ADM_PORTFOLIO",
                )
            ]
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_rejected_portfolio", language),
                    callback_data="ADM_PORTFOLIO_REJECTED",
                )
            ]
        )

    if roles.intersection(ADMIN_PAYMENT_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_pending_payments", language),
                    callback_data="ADM_PAYMENTS",
                )
            ]
        )

    if roles.intersection(ADMIN_LOG_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_logs", language),
                    callback_data="ADM_LOGS",
                )
            ]
        )

    if roles.intersection(ADMIN_ROLE_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_roles", language),
                    callback_data="ADM_ROLES",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="ADM_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)



def admin_roles_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_role_grant", language),
                    callback_data="ADM_ROLE_GRANT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_role_revoke", language),
                    callback_data="ADM_ROLE_REVOKE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_PANEL",
                )
            ],
        ]
    )


def parse_role_command(text: str | None) -> tuple[str, str, str] | None:
    parts = (text or "").strip().split(maxsplit=2)
    if len(parts) < 2:
        return None

    telegram_id = parts[0].strip()
    role = parts[1].strip().lower()
    reason = parts[2].strip() if len(parts) >= 3 else "manual role change from Telegram admin panel"

    if not telegram_id or not role:
        return None

    return telegram_id, role, reason

def short_uuid(value) -> str:
    return str(value)[:8] if value else "-"


def format_event_log_item(event: EventLog, *, language: str) -> str:
    created_at = event.created_at.strftime("%Y-%m-%d %H:%M") if event.created_at else "-"
    return (
        f"{created_at}\n"
        f"{event.event_type}\n"
        f"{event.entity_type or '-'}:{short_uuid(event.entity_id)}\n"
        f"trace: {event.trace_id or '-'}"
    )


def format_admin_action_item(action: AdminAction, *, language: str) -> str:
    created_at = action.created_at.strftime("%Y-%m-%d %H:%M") if action.created_at else "-"
    return (
        f"{created_at}\n"
        f"{action.action_type}\n"
        f"{action.target_type}:{short_uuid(action.target_id)}\n"
        f"{action.reason}"
    )


def format_logs_message(
    *,
    admin_actions: list[AdminAction],
    events: list[EventLog],
    include_admin_actions: bool,
    language: str,
) -> str:
    parts = [t("admin_logs_title", language)]

    if include_admin_actions:
        parts.append(f"\n{t('admin_logs_full_section', language)}:")
        if admin_actions:
            parts.extend(format_admin_action_item(item, language=language) for item in admin_actions)
        else:
            parts.append(t("admin_logs_empty", language))

    parts.append(f"\n{t('admin_logs_events_section', language)}:")
    if events:
        parts.extend(format_event_log_item(item, language=language) for item in events)
    else:
        parts.append(t("admin_logs_empty", language))

    return "\n\n".join(parts)

def pending_specialist_keyboard(index: int, total: int, language: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_approve", language),
                callback_data=f"ADM_SP_APPROVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_reject", language),
                callback_data=f"ADM_SP_REJECT:{index}",
            ),
        ],
    ]

    nav = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_SP_VIEW:{index - 1}",
            )
        )
    if index + 1 < total:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_SP_VIEW:{index + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def complaint_keyboard(index: int, total: int, language: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_resolve_complaint", language),
                callback_data=f"ADM_CP_RESOLVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_reject_complaint", language),
                callback_data=f"ADM_CP_REJECT:{index}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("admin_block_user", language),
                callback_data=f"ADM_CP_BLOCK:{index}",
            )
        ],
    ]

    nav = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_CP_VIEW:{index - 1}",
            )
        )
    if index + 1 < total:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_CP_VIEW:{index + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def review_keyboard(index: int, total: int, language: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_approve", language),
                callback_data=f"ADM_RV_APPROVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_reject", language),
                callback_data=f"ADM_RV_REJECT:{index}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("admin_hide_review", language),
                callback_data=f"ADM_RV_HIDE:{index}",
            )
        ],
    ]

    nav = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_RV_VIEW:{index - 1}",
            )
        )
    if index + 1 < total:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_RV_VIEW:{index + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def portfolio_moderation_keyboard(
    *,
    index: int,
    total: int,
    signed_url: str,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("portfolio_open_button", language),
                url=signed_url,
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_approve", language),
                callback_data=f"ADM_PORT_APPROVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_reject", language),
                callback_data=f"ADM_PORT_REJECT:{index}",
            ),
        ],
    ]

    nav = []

    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_PORT_VIEW:{index - 1}",
            )
        )

    if index + 1 < total:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PORT_VIEW:{index + 1}",
            )
        )

    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def rejected_portfolio_keyboard(
    *,
    index: int,
    total: int,
    signed_url: str,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("portfolio_open_button", language),
                url=signed_url,
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_restore_portfolio", language),
                callback_data=f"ADM_PORT_RESTORE:{index}",
            )
        ],
    ]

    navigation = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_PORT_REJECTED_VIEW:{index - 1}",
            )
        )

    if index + 1 < total:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PORT_REJECTED_VIEW:{index + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_pending_specialist_card(
    specialist: Specialist,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    price = t("search_price_not_set", language)
    if specialist.price_from is not None and specialist.price_to is not None:
        price = f"{specialist.price_from}-{specialist.price_to} {specialist.currency}"
    elif specialist.price_from is not None:
        price = f"{specialist.price_from}+ {specialist.currency}"

    return (
        f"{t('admin_pending_profile_title', language).format(index=index + 1, total=total)}\n\n"
        f"{specialist.display_name}\n"
        f"{t('search_filter_price_label', language)}: {price}\n"
        f"{t('admin_status', language)}: {specialist.status}\n\n"
        f"{specialist.short_description}"
    )

def pending_payment_keyboard(index: int, total: int, language: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_mark_payment_paid", language),
                callback_data=f"ADM_PAY_PAID:{index}",
            )
        ],
    ]

    nav = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_PAY_VIEW:{index - 1}",
            )
        )
    if index + 1 < total:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PAY_VIEW:{index + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_pending_payment_card(
    payment: Payment,
    invoice: Invoice | None,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    invoice_status = invoice.status if invoice else t("admin_item_not_found", language)
    invoice_id = invoice.id if invoice else payment.invoice_id

    return (
        f"{t('admin_pending_payment_title', language).format(index=index + 1, total=total)}\n\n"
        f"{t('billing_invoice_id', language)}: {invoice_id}\n"
        f"{t('billing_amount', language)}: {payment.amount} {payment.currency}\n"
        f"{t('admin_status', language)}: {payment.status}\n"
        f"{t('admin_invoice_status', language)}: {invoice_status}\n"
        f"{t('billing_payment_method', language)}: {payment.payment_method}"
    )

def format_complaint_card(
    complaint: Complaint,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    comment = complaint.comment or t("admin_no_comment", language)
    return (
        f"{t('admin_complaint_title', language).format(index=index + 1, total=total)}\n\n"
        f"{t('admin_status', language)}: {complaint.status}\n"
        f"{t('admin_complaint_target', language)}: {complaint.target_type}\n"
        f"{t('admin_complaint_reason', language)}: {complaint.reason}\n"
        f"{t('admin_complaint_comment', language)}: {comment}"
    )

def format_review_card(
    review: Review,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    review_text = review.text or t("admin_no_comment", language)
    reply_text = review.specialist_reply or t("admin_no_comment", language)

    return (
        f"{t('admin_review_title', language).format(index=index + 1, total=total)}\n\n"
        f"{t('admin_status', language)}: {review.status}\n"
        f"{t('admin_review_rating', language)}: {review.rating}/5\n"
        f"{t('admin_review_target', language)}: {review.target_type}:{str(review.target_id)[:8]}\n"
        f"{t('admin_review_context', language)}: {review.context_type or '-'}:{str(review.context_id)[:8] if review.context_id else '-'}\n\n"
        f"{t('admin_review_text', language)}:\n{review_text}\n\n"
        f"{t('admin_review_reply', language)}:\n{reply_text}"
    )

def format_portfolio_moderation_card(
    view,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    file_type = t(
        (
            "portfolio_photo_label"
            if view.storage_object.file_type == "photo"
            else "portfolio_pdf_label"
        ),
        language,
    )

    size_kb = round(
        (view.storage_object.size_bytes or 0) / 1024,
        1,
    )

    return (
        f"{t('admin_portfolio_title', language).format(index=index + 1, total=total)}\n\n"
        f"{t('admin_status', language)}: {view.item.status}\n"
        f"{t('admin_portfolio_type', language)}: {file_type}\n"
        f"{t('admin_portfolio_specialist', language)}: {str(view.item.specialist_id)[:8]}\n"
        f"{t('admin_portfolio_size', language)}: {size_kb} KB\n"
        f"{view.item.title or file_type}"
    )

async def show_admin_panel(message_or_callback, state: FSMContext | None = None):
    user = message_or_callback.from_user
    language = normalize_language(user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(user.id)
    if not admin_user_id or not roles:
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.answer(t("admin_access_denied", language), show_alert=True)
        else:
            await message_or_callback.answer(t("admin_access_denied", language))
        return

    if state:
        await state.clear()

    target_message = (
        message_or_callback.message
        if isinstance(message_or_callback, CallbackQuery)
        else message_or_callback
    )
    panel_text = t("admin_panel_title", language)
    if not (
        roles.intersection(ADMIN_MODERATION_MENU_ROLES)
        or roles.intersection(ADMIN_PAYMENT_MENU_ROLES)
        or roles.intersection(ADMIN_ROLE_MENU_ROLES)
        or roles.intersection(ADMIN_LOG_MENU_ROLES)
    ):
        panel_text = t("admin_no_available_actions", language)

    await target_message.answer(
        panel_text,
        reply_markup=admin_panel_keyboard(language, roles),
    )

    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.answer()


@admin_router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    await show_admin_panel(message, state)


@admin_router.callback_query(F.data == "ADM_PANEL")
async def admin_panel_callback(callback: CallbackQuery, state: FSMContext):
    await show_admin_panel(callback, state)

@admin_router.callback_query(F.data == "ADM_ROLES")
async def admin_roles_panel(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_ROLE_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    await state.clear()
    await callback.message.answer(
        t("admin_roles_title", language),
        reply_markup=admin_roles_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_LOGS")
async def admin_logs_panel(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not roles.intersection(ADMIN_LOG_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    include_admin_actions = bool(roles.intersection({"super_admin", "admin"}))

    try:
        async with get_session() as session:
            service = ModerationService(ModerationRepository(session))
            events = await service.list_recent_event_logs(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                limit=5,
            )
            admin_actions = []
            if include_admin_actions:
                admin_actions = await service.list_recent_admin_actions(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    limit=5,
                )
    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.clear()
    await callback.message.answer(
        format_logs_message(
            admin_actions=admin_actions,
            events=events,
            include_admin_actions=include_admin_actions,
            language=language,
        ),
        reply_markup=admin_panel_keyboard(language, roles),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_ROLE_GRANT")
async def ask_role_grant(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_ROLE_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    await state.set_state(AdminModerationFSM.entering_role_grant)
    await callback.message.answer(t("admin_role_grant_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_role_grant)
async def receive_role_grant(message: Message, state: FSMContext):
    language = normalize_language(message.from_user.language_code)
    parsed = parse_role_command(message.text)

    if not parsed:
        await message.answer(t("admin_role_bad_format", language))
        return

    target_platform_user_id, role, reason = parsed
    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).grant_admin_role(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_platform_user_id=target_platform_user_id,
                role=role,
                reason=reason,
            )
    except ModerationError as exc:
        await message.answer(str(exc))
        return

    await state.clear()
    await message.answer(
        t("admin_role_granted", language).format(
            role=role,
            status=result.status,
        ),
        reply_markup=admin_roles_keyboard(language),
    )


@admin_router.callback_query(F.data == "ADM_ROLE_REVOKE")
async def ask_role_revoke(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_ROLE_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    await state.set_state(AdminModerationFSM.entering_role_revoke)
    await callback.message.answer(t("admin_role_revoke_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_role_revoke)
async def receive_role_revoke(message: Message, state: FSMContext):
    language = normalize_language(message.from_user.language_code)
    parsed = parse_role_command(message.text)

    if not parsed:
        await message.answer(t("admin_role_bad_format", language))
        return

    target_platform_user_id, role, reason = parsed
    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).revoke_admin_role(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_platform_user_id=target_platform_user_id,
                role=role,
                reason=reason,
            )
    except ModerationError as exc:
        await message.answer(str(exc))
        return

    await state.clear()
    await message.answer(
        t("admin_role_revoked", language).format(
            role=role,
            status=result.status,
        ),
        reply_markup=admin_roles_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_MENU")
async def admin_to_menu(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    await state.clear()
    await callback.message.answer(
        t("search_main_menu", language),
        reply_markup=get_main_menu_keyboard(language),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "ADM_PENDING")
async def list_pending_profiles(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not roles.intersection(ADMIN_MODERATION_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return
    try:
        async with get_session() as session:
            service = ModerationService(ModerationRepository(session))
            specialists = await service.list_pending_specialists(
                admin_user_id=admin_user_id,
                limit=10,
            )
    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not specialists:
        await callback.message.answer(
            t("admin_no_pending_profiles", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    await state.update_data(
        admin_pending_specialist_ids=[str(item.id) for item in specialists],
    )
    await show_pending_specialist(callback, state, index=0)


async def show_pending_specialist(callback: CallbackQuery, state: FSMContext, index: int):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_pending_specialist_ids") or []

    if not ids:
        await callback.message.answer(
            t("admin_no_pending_profiles", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    index = max(0, min(int(index), len(ids) - 1))

    async with get_session() as session:
        specialist = await session.get(Specialist, UUID(ids[index]))

    if not specialist:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        format_pending_specialist_card(
            specialist,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=pending_specialist_keyboard(index, len(ids), language),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("ADM_SP_VIEW:"))
async def view_pending_specialist(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_pending_specialist(callback, state, index=index)


@admin_router.callback_query(F.data.startswith("ADM_SP_APPROVE:"))
async def approve_pending_specialist(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_pending_specialist_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not roles:
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    try:
        specialist_id = UUID(ids[index])
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).approve_specialist(
                admin_user_id=admin_user_id,
                specialist_id=specialist_id,
                reason="approved from Telegram admin panel",
            )

        logger.info(
            "admin_specialist_approved telegram_id=%s admin_user_id=%s specialist_id=%s status=%s",
            callback.from_user.id,
            admin_user_id,
            specialist_id,
            result.status,
        )
    except ModerationError as exc:
        logger.warning(
            "admin_specialist_approve_failed telegram_id=%s admin_user_id=%s specialist_id=%s error=%s",
            callback.from_user.id,
            admin_user_id,
            ids[index],
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        t("admin_specialist_approved", language).format(status=result.status),
        reply_markup=admin_panel_keyboard(language),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("ADM_SP_REJECT:"))
async def ask_reject_specialist_reason(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_pending_specialist_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await state.update_data(admin_reject_specialist_id=ids[index])
    await state.set_state(AdminModerationFSM.entering_reject_reason)
    await callback.message.answer(t("admin_reason_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_reject_reason)
async def receive_reject_specialist_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()
    specialist_id = data.get("admin_reject_specialist_id")

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)
    if not admin_user_id or not roles or not specialist_id:
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        specialist_uuid = UUID(specialist_id)
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).reject_specialist(
                admin_user_id=admin_user_id,
                specialist_id=specialist_uuid,
                reason=reason,
            )

        logger.info(
            "admin_specialist_rejected telegram_id=%s admin_user_id=%s specialist_id=%s status=%s",
            message.from_user.id,
            admin_user_id,
            specialist_uuid,
            result.status,
        )
    except ModerationError as exc:
        logger.warning(
            "admin_specialist_reject_failed telegram_id=%s admin_user_id=%s specialist_id=%s error=%s",
            message.from_user.id,
            admin_user_id,
            specialist_id,
            exc,
        )
        await message.answer(str(exc))
        return

    await state.clear()
    await message.answer(
        t("admin_specialist_rejected", language).format(status=result.status),
        reply_markup=admin_panel_keyboard(language),
    )


@admin_router.callback_query(F.data == "ADM_COMPLAINTS")
async def list_open_complaints(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not roles.intersection(ADMIN_MODERATION_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            complaints = await ModerationService(
                ModerationRepository(session)
            ).list_open_complaints(
                admin_user_id=admin_user_id,
                limit=10,
            )
    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not complaints:
        await callback.message.answer(
            t("admin_no_open_complaints", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    await state.update_data(admin_complaint_ids=[str(item.id) for item in complaints])
    await show_complaint(callback, state, index=0)


async def show_complaint(callback: CallbackQuery, state: FSMContext, index: int):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_complaint_ids") or []

    if not ids:
        await callback.message.answer(
            t("admin_no_open_complaints", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    index = max(0, min(int(index), len(ids) - 1))

    async with get_session() as session:
        complaint = await session.get(Complaint, UUID(ids[index]))

    if not complaint:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        format_complaint_card(
            complaint,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=complaint_keyboard(index, len(ids), language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_REVIEWS")
async def list_pending_reviews(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not roles.intersection(ADMIN_MODERATION_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            reviews = await ReviewService(
                ReviewRepository(session)
            ).list_pending_reviews(limit=10)
    except ReviewServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not reviews:
        await callback.message.answer(
            t("admin_no_pending_reviews", language),
            reply_markup=admin_panel_keyboard(language, roles),
        )
        await callback.answer()
        return

    await state.update_data(admin_review_ids=[str(item.id) for item in reviews])
    await show_review(callback, state, index=0)


async def show_review(callback: CallbackQuery, state: FSMContext, index: int):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_review_ids") or []

    if not ids:
        await callback.message.answer(
            t("admin_no_pending_reviews", language),
            reply_markup=admin_panel_keyboard(language, ADMIN_MODERATION_MENU_ROLES),
        )
        await callback.answer()
        return

    index = max(0, min(int(index), len(ids) - 1))

    async with get_session() as session:
        review = await session.get(Review, UUID(ids[index]))

    if not review:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        format_review_card(
            review,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=review_keyboard(index, len(ids), language),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("ADM_RV_VIEW:"))
async def view_pending_review(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_review(callback, state, index=index)


@admin_router.callback_query(F.data.startswith("ADM_RV_APPROVE:"))
async def approve_pending_review(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_review_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_MODERATION_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    review_id = UUID(ids[index])
    reason = "approved from Telegram admin panel"

    try:
        async with get_session() as session:
            result = await ReviewService(
                ReviewRepository(session)
            ).moderate_review(
                review_id=review_id,
                status="published",
                reason=reason,
            )

            await ModerationRepository(session).log_admin_action(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                action_type="publish_review",
                target_type="review",
                target_id=review_id,
                before_state={},
                after_state={"status": result.review.status},
                reason=reason,
            )
            await ModerationRepository(session).log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="review_published",
                entity_type="review",
                entity_id=review_id,
                payload={"reason": reason},
            )
            await session.commit()
    except ReviewServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        t("admin_review_updated", language).format(status="published"),
        reply_markup=admin_panel_keyboard(language, roles),
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_RV_REJECT:"))
async def ask_reject_review_reason(callback: CallbackQuery, state: FSMContext):
    await prepare_review_moderation_reason(
        callback,
        state,
        status="rejected",
        state_name=AdminModerationFSM.entering_review_reject_reason,
    )


@admin_router.callback_query(F.data.startswith("ADM_RV_HIDE:"))
async def ask_hide_review_reason(callback: CallbackQuery, state: FSMContext):
    await prepare_review_moderation_reason(
        callback,
        state,
        status="hidden",
        state_name=AdminModerationFSM.entering_review_hide_reason,
    )


async def prepare_review_moderation_reason(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    status: str,
    state_name: State,
):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_review_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await state.update_data(
        admin_review_action_id=ids[index],
        admin_review_action_status=status,
    )
    await state.set_state(state_name)
    await callback.message.answer(t("admin_reason_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_review_reject_reason)
@admin_router.message(AdminModerationFSM.entering_review_hide_reason)
async def receive_review_moderation_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()
    review_id = data.get("admin_review_action_id")
    status = data.get("admin_review_action_status")

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    if status not in {"rejected", "hidden"} or not review_id:
        await message.answer(t("admin_item_not_found", language))
        await state.clear()
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_MODERATION_MENU_ROLES):
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    review_uuid = UUID(review_id)

    try:
        async with get_session() as session:
            result = await ReviewService(
                ReviewRepository(session)
            ).moderate_review(
                review_id=review_uuid,
                status=status,
                reason=reason,
            )

            action_type = "hide_review" if status == "hidden" else "reject_review"
            event_type = "review_hidden" if status == "hidden" else "review_rejected"

            await ModerationRepository(session).log_admin_action(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                action_type=action_type,
                target_type="review",
                target_id=review_uuid,
                before_state={},
                after_state={"status": result.review.status},
                reason=reason,
            )
            await ModerationRepository(session).log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type=event_type,
                entity_type="review",
                entity_id=review_uuid,
                payload={"reason": reason},
            )
            await session.commit()
    except ReviewServiceError as exc:
        await message.answer(str(exc))
        return

    await state.clear()
    await message.answer(
        t("admin_review_updated", language).format(status=status),
        reply_markup=admin_panel_keyboard(language, roles),
    )

@admin_router.callback_query(F.data.startswith("ADM_CP_VIEW:"))
async def view_complaint(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_complaint(callback, state, index=index)


@admin_router.callback_query(F.data.startswith("ADM_CP_RESOLVE:"))
async def ask_resolve_complaint_reason(callback: CallbackQuery, state: FSMContext):
    await prepare_complaint_resolution(callback, state, status="resolved")


@admin_router.callback_query(F.data.startswith("ADM_CP_REJECT:"))
async def ask_reject_complaint_reason(callback: CallbackQuery, state: FSMContext):
    await prepare_complaint_resolution(callback, state, status="rejected")


async def prepare_complaint_resolution(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    status: str,
):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_complaint_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await state.update_data(
        admin_complaint_id=ids[index],
        admin_complaint_resolution_status=status,
    )
    await state.set_state(AdminModerationFSM.entering_complaint_resolution_reason)
    await callback.message.answer(t("admin_reason_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_complaint_resolution_reason)
async def receive_complaint_resolution_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()
    complaint_id = data.get("admin_complaint_id")
    status = data.get("admin_complaint_resolution_status") or "resolved"

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)
    if not admin_user_id or not roles or not complaint_id:
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        complaint_uuid = UUID(complaint_id)
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).resolve_complaint(
                admin_user_id=admin_user_id,
                complaint_id=complaint_uuid,
                status=status,
                reason=reason,
            )

        logger.info(
            "admin_complaint_updated telegram_id=%s admin_user_id=%s complaint_id=%s status=%s",
            message.from_user.id,
            admin_user_id,
            complaint_uuid,
            result.status,
        )
    except ModerationError as exc:
        logger.warning(
            "admin_complaint_update_failed telegram_id=%s admin_user_id=%s complaint_id=%s status=%s error=%s",
            message.from_user.id,
            admin_user_id,
            complaint_id,
            status,
            exc,
        )
        await message.answer(str(exc))
        return

    await state.clear()
    await message.answer(
        t("admin_complaint_updated", language).format(status=result.status),
        reply_markup=admin_panel_keyboard(language),
    )


@admin_router.callback_query(F.data.startswith("ADM_CP_BLOCK:"))
async def ask_block_user_reason(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_complaint_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    async with get_session() as session:
        complaint = await session.get(Complaint, UUID(ids[index]))

    if not complaint:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    if complaint.target_type == "user":
        target_user_id = complaint.target_id
    elif complaint.target_type == "specialist":
        async with get_session() as session:
            specialist = await session.get(Specialist, complaint.target_id)
            target_user_id = specialist.user_id if specialist else None
    else:
        target_user_id = None

    if not target_user_id:
        await callback.answer(t("admin_block_target_not_found", language), show_alert=True)
        return

    await state.update_data(admin_block_user_id=str(target_user_id))
    await state.set_state(AdminModerationFSM.entering_block_reason)
    await callback.message.answer(t("admin_reason_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_block_reason)
async def receive_block_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()
    user_id = data.get("admin_block_user_id")

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)
    if not admin_user_id or not roles or not user_id:
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        user_uuid = UUID(user_id)
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).block_user(
                admin_user_id=admin_user_id,
                user_id=user_uuid,
                reason=reason,
            )

        logger.info(
            "admin_user_blocked telegram_id=%s admin_user_id=%s user_id=%s status=%s",
            message.from_user.id,
            admin_user_id,
            user_uuid,
            result.status,
        )
    except ModerationError as exc:
        logger.warning(
            "admin_user_block_failed telegram_id=%s admin_user_id=%s user_id=%s error=%s",
            message.from_user.id,
            admin_user_id,
            user_id,
            exc,
        )
        await message.answer(str(exc))
        return

    await state.clear()
    await message.answer(
        t("admin_user_blocked", language).format(status=result.status),
        reply_markup=admin_panel_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_PAYMENTS")
async def list_pending_payments(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not roles.intersection(ADMIN_PAYMENT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            payments = await BillingService(
                BillingRepository(session)
            ).list_pending_manual_payments(
                admin_user_id=admin_user_id,
                limit=10,
            )

        logger.info(
            "admin_pending_payments_listed telegram_id=%s admin_user_id=%s count=%s",
            callback.from_user.id,
            admin_user_id,
            len(payments),
        )
    except BillingError as exc:
        logger.warning(
            "admin_pending_payments_list_failed telegram_id=%s admin_user_id=%s error=%s",
            callback.from_user.id,
            admin_user_id,
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return

    if not payments:
        await callback.message.answer(
            t("admin_no_pending_payments", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    await state.update_data(admin_payment_ids=[str(item.id) for item in payments])
    await show_pending_payment(callback, state, index=0)


async def show_pending_payment(callback: CallbackQuery, state: FSMContext, index: int):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_payment_ids") or []

    if not ids:
        await callback.message.answer(
            t("admin_no_pending_payments", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    index = max(0, min(int(index), len(ids) - 1))

    async with get_session() as session:
        payment = await session.get(Payment, UUID(ids[index]))
        invoice = await session.get(Invoice, payment.invoice_id) if payment else None

    if not payment:
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await callback.message.answer(
        format_pending_payment_card(
            payment,
            invoice,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=pending_payment_keyboard(index, len(ids), language),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("ADM_PAY_VIEW:"))
async def view_pending_payment(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_pending_payment(callback, state, index=index)


@admin_router.callback_query(F.data.startswith("ADM_PAY_PAID:"))
async def ask_mark_payment_paid_reason(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_payment_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await state.update_data(admin_payment_id=ids[index])
    await state.set_state(AdminModerationFSM.entering_payment_paid_reason)
    await callback.message.answer(t("admin_reason_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_payment_paid_reason)
async def receive_mark_payment_paid_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()
    payment_id = data.get("admin_payment_id")

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)
    if not admin_user_id or not roles or not payment_id:
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        payment_uuid = UUID(payment_id)
        async with get_session() as session:
            result = await BillingService(
                BillingRepository(session)
            ).mark_payment_paid(
                admin_user_id=admin_user_id,
                payment_id=payment_uuid,
                reason=reason,
            )

        logger.info(
            "admin_payment_mark_paid telegram_id=%s admin_user_id=%s payment_id=%s approval_required=%s payment_status=%s",
            message.from_user.id,
            admin_user_id,
            payment_uuid,
            result.approval_required,
            result.payment.status,
        )
    except BillingError as exc:
        logger.warning(
            "admin_payment_mark_paid_failed telegram_id=%s admin_user_id=%s payment_id=%s error=%s",
            message.from_user.id,
            admin_user_id,
            payment_id,
            exc,
        )
        await message.answer(str(exc))
        return

    await state.clear()

    if result.approval_required:
        text = t("admin_payment_approval_required", language)
    else:
        text = t("admin_payment_marked_paid", language).format(
            invoice_status=result.invoice.status,
            payment_status=result.payment.status,
            promotion_status=result.promotion.status if result.promotion else "-",
        )

    await message.answer(
        text,
        reply_markup=admin_panel_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_PORTFOLIO")
async def list_pending_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await PortfolioService(
                PortfolioRepository(session)
            ).list_pending_items(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                limit=50,
            )
    except PortfolioServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not items:
        await callback.message.answer(
            t("admin_no_pending_portfolio", language),
            reply_markup=admin_panel_keyboard(language, roles),
        )
        await callback.answer()
        return

    await state.update_data(
        admin_portfolio_ids=[
            str(view.item.id)
            for view in items
        ]
    )

    await show_pending_portfolio_item(
        callback,
        state,
        index=0,
    )


async def show_pending_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    index: int,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    ids = data.get("admin_portfolio_ids") or []

    if not ids:
        await callback.answer(
            t("admin_no_pending_portfolio", language),
            show_alert=True,
        )
        return

    index = max(0, min(int(index), len(ids) - 1))
    item_id = UUID(ids[index])

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await PortfolioService(
                PortfolioRepository(session)
            ).list_pending_items(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                limit=50,
            )
    except PortfolioServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    view = next(
        (
            candidate
            for candidate in items
            if candidate.item.id == item_id
        ),
        None,
    )

    if not view:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    text = format_portfolio_moderation_card(
        view,
        index=index,
        total=len(ids),
        language=language,
    )

    keyboard = portfolio_moderation_keyboard(
        index=index,
        total=len(ids),
        signed_url=view.signed_url,
        language=language,
    )

    if view.storage_object.file_type == "photo":
        await callback.message.answer_photo(
            photo=view.signed_url,
            caption=text,
            reply_markup=keyboard,
        )
    else:
        await callback.message.answer(
            text,
            reply_markup=keyboard,
        )

    await callback.answer()

@admin_router.callback_query(F.data == "ADM_PORTFOLIO_REJECTED")
async def list_rejected_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await PortfolioService(
                PortfolioRepository(session)
            ).list_rejected_items(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                limit=50,
            )
    except PortfolioServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if not items:
        await callback.message.answer(
            t("admin_no_rejected_portfolio", language),
            reply_markup=admin_panel_keyboard(language, roles),
        )
        await callback.answer()
        return

    await state.update_data(
        admin_rejected_portfolio_ids=[
            str(view.item.id)
            for view in items
        ]
    )

    await show_rejected_portfolio_item(
        callback,
        state,
        index=0,
    )


async def show_rejected_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    index: int,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    ids = data.get("admin_rejected_portfolio_ids") or []

    if not ids:
        await callback.answer(
            t("admin_no_rejected_portfolio", language),
            show_alert=True,
        )
        return

    index = max(0, min(int(index), len(ids) - 1))
    item_id = UUID(ids[index])

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await PortfolioService(
                PortfolioRepository(session)
            ).list_rejected_items(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                limit=50,
            )
    except PortfolioServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    view = next(
        (
            candidate
            for candidate in items
            if candidate.item.id == item_id
        ),
        None,
    )

    if not view:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    text = format_portfolio_moderation_card(
        view,
        index=index,
        total=len(ids),
        language=language,
    )

    keyboard = rejected_portfolio_keyboard(
        index=index,
        total=len(ids),
        signed_url=view.signed_url,
        language=language,
    )

    if view.storage_object.file_type == "photo":
        await callback.message.answer_photo(
            photo=view.signed_url,
            caption=text,
            reply_markup=keyboard,
        )
    else:
        await callback.message.answer(
            text,
            reply_markup=keyboard,
        )

    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("ADM_PORT_REJECTED_VIEW:")
)
async def view_rejected_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    try:
        index = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            t(
                "admin_item_not_found",
                normalize_language(callback.from_user.language_code),
            ),
            show_alert=True,
        )
        return

    await show_rejected_portfolio_item(
        callback,
        state,
        index=index,
    )

@admin_router.callback_query(
    F.data.startswith("ADM_PORT_RESTORE:")
)
async def restore_rejected_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    ids = data.get("admin_rejected_portfolio_ids") or []

    try:
        index = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if index < 0 or index >= len(ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    item_id = UUID(ids[index])

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            service = PortfolioService(
                PortfolioRepository(session)
            )

            item = await service.approve_item(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                item_id=item_id,
            )

            moderation_repository = ModerationRepository(session)

            await moderation_repository.log_admin_action(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                action_type="restore_portfolio_item",
                target_type="specialist_portfolio_item",
                target_id=item_id,
                before_state={"status": "rejected"},
                after_state={"status": item.status},
                reason="portfolio restored after repeated review",
            )

            await moderation_repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="portfolio_item_restored",
                entity_type="specialist_portfolio_item",
                entity_id=item_id,
                payload={
                    "previous_status": "rejected",
                    "status": item.status,
                },
            )

            await session.commit()

    except PortfolioServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    remaining_ids = [
        stored_id
        for stored_id in ids
        if stored_id != str(item_id)
    ]

    await state.update_data(
        admin_rejected_portfolio_ids=remaining_ids
    )

    await callback.message.answer(
        t("admin_portfolio_updated", language).format(
            status=item.status,
        ),
        reply_markup=admin_panel_keyboard(language, roles),
    )

    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_PORT_VIEW:"))
async def view_pending_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    index = int(callback.data.split(":", 1)[1])

    await show_pending_portfolio_item(
        callback,
        state,
        index=index,
    )


async def moderate_pending_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    status: str,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    ids = data.get("admin_portfolio_ids") or []

    index = int(callback.data.split(":", 1)[1])

    if index < 0 or index >= len(ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    item_id = UUID(ids[index])

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    reason = f"portfolio {status} from Telegram admin panel"

    try:
        async with get_session() as session:
            service = PortfolioService(
                PortfolioRepository(session)
            )

            if status == "active":
                item = await service.approve_item(
                    tenant_id=tenant_id,
                    moderator_user_id=admin_user_id,
                    item_id=item_id,
                )
            else:
                item = await service.reject_item(
                    tenant_id=tenant_id,
                    moderator_user_id=admin_user_id,
                    item_id=item_id,
                )

            moderation_repository = ModerationRepository(session)

            await moderation_repository.log_admin_action(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                action_type=f"{status}_portfolio_item",
                target_type="specialist_portfolio_item",
                target_id=item_id,
                before_state={"status": "pending_moderation"},
                after_state={"status": item.status},
                reason=reason,
            )

            await moderation_repository.log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type=f"portfolio_item_{status}",
                entity_type="specialist_portfolio_item",
                entity_id=item_id,
                payload={"reason": reason},
            )

            await session.commit()

    except PortfolioServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    remaining_ids = [
        stored_id
        for stored_id in ids
        if stored_id != str(item_id)
    ]

    await state.update_data(
        admin_portfolio_ids=remaining_ids
    )

    await callback.message.answer(
        t("admin_portfolio_updated", language).format(
            status=status
        ),
        reply_markup=admin_panel_keyboard(language, roles),
    )

    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("ADM_PORT_APPROVE:")
)
async def approve_pending_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    await moderate_pending_portfolio_item(
        callback,
        state,
        status="active",
    )


@admin_router.callback_query(
    F.data.startswith("ADM_PORT_REJECT:")
)
async def reject_pending_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
):
    await moderate_pending_portfolio_item(
        callback,
        state,
        status="rejected",
    )