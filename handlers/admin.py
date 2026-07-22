import logging
from uuid import UUID
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from database.repositories.dictionaries import DictionaryRepository
from services.dictionaries import DictionaryService, DictionaryServiceError
from database.models import (
    AdminAction,
    Complaint,
    EventLog,
    Review,
    Specialist,
)
from services.specialist import (
    MAX_PROFESSIONS_PER_CATEGORY,
    SpecialistService,
)
from database.repositories.moderation import ModerationRepository
from database.repositories.billing import BillingRepository
from database.repositories.contact import ContactChatRepository
from database.repositories.event import EventRepository
from database.repositories.reviews import ReviewRepository
from database.repositories.portfolio import PortfolioRepository
from database.repositories.support import SupportRepository
from database.repositories.specialist import SpecialistRepository
from database.session import get_session
from handlers.start import get_main_menu_keyboard_for_user, normalize_language, open_current_role_cabinet, send_global_main_menu
from services.moderation import (
    ImpersonationRoleUnavailableError,
    ModerationError,
    SuperAdminRoleScopeCard,
    ModerationService,
    AdminMenuSummary,
    ModeratorComplaintQueueCard,
    ModeratorComplaintCard,
    ModeratorScopedBlacklistCard,
    AdminUserSearchCard,
    AdminUserDetailsCard,
    AdminSpecialistPage,
    AdminUserHistoryCard,
    AdminGlobalBlacklistCard,
    AdminAuditCard,
)
from services.billing import (
    BillingError,
    BillingService,
    PendingManualPaymentCard,
)
from services.contact_chat import (
    ContactChatError,
    ContactChatService,
)
from services.reviews import (
    ReviewModerationCard,
    ReviewService,
    ReviewServiceError,
)
from services.user import UserService
from services.portfolio import PortfolioService, PortfolioServiceError
from services.support import (
    AdminEscalatedTicketPage,
    SupportService,
    SupportServiceError,
)
from ui.texts import t
from utils.telegram_cleanup import (
    delete_telegram_messages,
    edit_or_replace_menu_message,
    edit_or_replace_tracked_menu_message,
)
from handlers.search import format_chat_message_body

admin_router = Router()
logger = logging.getLogger(__name__)

async def replace_admin_input_screen(
    *,
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    data = await state.get_data()

    await delete_telegram_messages(
        bot=message.bot,
        chat_id=message.chat.id,
        message_ids=[
            message.message_id
        ],
    )

    menu_message_id = (
        await edit_or_replace_tracked_menu_message(
            message=message,
            menu_message_id=data.get(
                "last_menu_message_id"
            ),
            text=text,
            reply_markup=reply_markup,
        )
    )

    await state.update_data(
        last_menu_message_id=menu_message_id,
    )


async def replace_admin_callback_screen(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    callback_answered: bool = False,
) -> None:
    if not callback_answered:
        await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=text,
        reply_markup=reply_markup,
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id,
    )


def review_moderation_error_text(
    error: Exception,
    language: str,
) -> str:
    if "no longer pending moderation" in str(error).lower():
        return t(
            "admin_review_already_processed",
            language,
        )

    return t(
        "admin_item_not_found",
        language,
    )

ADMIN_MODERATION_MENU_ROLES = {"super_admin", "admin", "moderator"}
ADMIN_PAYMENT_MENU_ROLES = {"super_admin", "admin", "finance_admin"}
ADMIN_ROLE_MENU_ROLES = {"super_admin"}
ADMIN_LOG_MENU_ROLES = {"super_admin", "admin"}
ADMIN_SUPPORT_MENU_ROLES = {"support"}
ADMIN_DICT_MENU_ROLES = {"super_admin"}
ADMIN_DIALOGS_MENU_ROLES = {"super_admin", "admin", "moderator"}
ADMIN_PROMOTION_MENU_ROLES = {"super_admin", "advertiser"}
ADMIN_SYSTEM_MENU_ROLES = {"super_admin"}
ADMIN_SUPPORT_STATS_ROLES = {"support", "admin", "super_admin"}
SUPPORT_STAFF_PAGE_SIZE = 5
ADMIN_ESCALATED_TICKET_PAGE_SIZE = 5
ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE = 5
ADMIN_AUDIT_PAGE_SIZE = 5
ADMIN_GLOBAL_BLACKLIST_ROLES = {"admin", "super_admin"}
MODERATOR_PROFILE_PAGE_SIZE = 5
ADMIN_SPECIALIST_PAGE_SIZE = 5
MODERATOR_PORTFOLIO_PAGE_SIZE = 5
ADMIN_CATEGORIES_PAGE_SIZE = 5
ADMIN_PROFESSIONS_PAGE_SIZE = 5
ADMIN_SKILLS_PAGE_SIZE = 5
ADMIN_LANGUAGES_PAGE_SIZE = 5
ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE = 5
ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE = 5
ADMIN_MOVE_CATEGORIES_PAGE_SIZE = 5
ADMIN_MOVE_PROFESSIONS_PAGE_SIZE = 5
ADMIN_COUNTRIES_PAGE_SIZE = 5
ADMIN_CITIES_PAGE_SIZE = 5
READ_ONLY_MODERATION_TARGET_ROLES = {
    "moderator",
    "admin",
}
READ_ONLY_CLIENT_PAGE_SIZE = 5
def effective_panel_roles(
    roles: set[str],
    active_role: str | None,
) -> set[str]:
    if active_role in roles:
        return {active_role}

    return roles
class AdminModerationFSM(StatesGroup):
    entering_admin_user_search = State()
    entering_super_admin_impersonation_admin_user_search = (
        State()
    )
    entering_admin_user_global_block_reason = State()
    confirming_admin_user_global_block = State()
    confirming_admin_user_global_block_final = State()
    entering_admin_user_global_unblock_reason = State()
    confirming_admin_user_global_unblock = State()
    confirming_admin_user_global_unblock_final = State()
    entering_specialist_decision_reason = State()
    confirming_specialist_decision = State()
    entering_specialist_visibility_reason = State()
    confirming_specialist_visibility = State()
    entering_specialist_changes_reason = State()
    entering_complaint_resolution_reason = State()
    entering_complaint_scoped_block_reason = State()
    confirming_complaint_scoped_block = State()
    entering_complaint_admin_reason = State()
    confirming_complaint_admin = State()
    entering_payment_paid_reason = State()
    entering_role_grant = State()
    entering_role_revoke = State()
    entering_review_hide_reason = State()
    entering_support_reply = State()
    entering_support_search = State()
    entering_support_escalation_reason = State()
    entering_admin_ticket_action_reason = State()
    confirming_specialist_changes = State()
    entering_specialist_scoped_block_reason = State()
    confirming_specialist_scoped_block = State()
    entering_portfolio_moderation_reason = State()
    confirming_portfolio_moderation = State()
    entering_blacklist_revoke_reason = State()
    confirming_blacklist_revoke = State()
    entering_blacklist_add_user = State()
    entering_blacklist_add_reason = State()
    confirming_blacklist_add = State()
    waiting_super_admin_user_search = State()
    entering_super_admin_role_grant = State()
    confirming_super_admin_role_grant = State()
    confirming_super_admin_role_grant_final = State()
    entering_super_admin_role_revoke = State()
    confirming_super_admin_role_revoke = State()
    confirming_super_admin_role_revoke_final = State()
    entering_super_admin_impersonation_reason = State()
    entering_super_admin_permission_search = State()
    entering_super_admin_permission_grant = State()
    confirming_super_admin_permission_grant = State()
    entering_super_admin_permission_revoke = State()
    confirming_super_admin_permission_revoke = State()
    entering_super_admin_global_blacklist_add = State()
    confirming_super_admin_global_blacklist_add = State()
    confirming_super_admin_global_blacklist_add_final = State()
    entering_super_admin_global_blacklist_revoke = State()
    confirming_super_admin_global_blacklist_revoke = State()
    confirming_super_admin_global_blacklist_revoke_final = State()
    entering_super_admin_scope_add = State()
    confirming_super_admin_scope_add = State()
    entering_super_admin_scope_revoke = State()
    confirming_super_admin_scope_revoke = State()
    entering_admin_category_number = State()
    entering_admin_category_rename = State()
    entering_admin_category_sort_order = State()
    entering_admin_category_create = State()
    entering_admin_profession_number = State()
    entering_admin_profession_create = State()
    entering_admin_profession_rename = State()
    entering_admin_profession_move = State()
    entering_admin_specialist_move_numbers = State()
    entering_admin_specialist_move_target = State()
    confirming_admin_specialist_move = State()
    entering_admin_category_specialist_move_numbers = State()
    entering_admin_category_specialist_move_target = State()
    confirming_admin_category_specialist_move = State()
    entering_admin_move_target_professions = State()
    choosing_admin_move_mode = State()
    confirming_admin_multi_move = State()
    entering_admin_skill_number = State()
    entering_admin_skill_create = State()
    entering_admin_skill_rename = State()
    entering_admin_skill_merge = State()
    confirming_admin_skill_merge = State()
    entering_admin_language_number = State()
    entering_admin_language_create = State()
    entering_admin_language_rename = State()
    entering_admin_country_number = State()
    entering_admin_country_create = State()
    entering_admin_country_update = State()
    entering_admin_city_number = State()
    entering_admin_city_create = State()
    entering_admin_city_update = State()
    entering_admin_city_geo_update = State()
    entering_admin_country_import = State()
    entering_admin_city_import = State()

async def get_admin_user_context(telegram_id: int | str):
    async with get_session() as session:
        user = await UserService(session).get_user_by_telegram_id(telegram_id)
        if not user:
            return None, None, set()

        service = ModerationService(ModerationRepository(session))
        roles = await service.get_admin_roles(user.id)
        return user.id, user.tenant_id, roles


def admin_panel_keyboard(
    language: str,
    roles: set[str] | None = None,
    *,
    show_role_switch: bool = False,
) -> InlineKeyboardMarkup:
    roles = roles or set()
    rows = []

    if roles.intersection(ADMIN_ROLE_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_users_roles_section_btn", language),
                    callback_data="SA_USERS",
                )
            ]
        )

    if roles.intersection(ADMIN_DICT_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_dictionaries_section_btn", language),
                    callback_data="ADM_DICT",
                )
            ]
        )

    if roles.intersection(ADMIN_MODERATION_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_moderation_section_btn", language),
                    callback_data="ADM_MODERATION_MENU",
                )
            ]
        )

    if roles.intersection(ADMIN_DIALOGS_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_dialogs_section_btn", language),
                    callback_data="ADM_DIALOGS_STUB",
                )
            ]
        )

    if roles.intersection(ADMIN_PAYMENT_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_finance_section_btn", language),
                    callback_data="SA_FINANCE",
                )
            ]
        )

    if roles.intersection(ADMIN_PROMOTION_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_promotion_section_btn", language),
                    callback_data="ADM_PROMOTION_STUB",
                )
            ]
        )

    if roles.intersection(ADMIN_SYSTEM_MENU_ROLES):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("admin_system_section_btn", language),
                    callback_data="SA_SYSTEM",
                )
            ]
        )

    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
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

def parse_super_admin_role_action(
    text: str | None,
) -> tuple[str, str] | None:
    parts = (text or "").strip().split(maxsplit=1)

    if len(parts) < 2:
        return None

    role = parts[0].strip().lower()
    reason = parts[1].strip()

    if not role or len(reason) < 3:
        return None

    return role, reason

def parse_super_admin_permission_action(
    text: str | None,
) -> tuple[str, str, str] | None:
    parts = (text or "").strip().split(maxsplit=2)

    if len(parts) != 3:
        return None

    role, permission_code, reason = parts

    return (
        role.strip().lower(),
        permission_code.strip(),
        reason.strip(),
    )

def super_admin_role_confirm_keyboard(
    action: str,
    *,
    danger: bool,
    language: str,
) -> InlineKeyboardMarkup:
    callback_prefix = (
        f"SA_ROLE_{action.upper()}_FINAL"
        if danger
        else f"SA_ROLE_{action.upper()}_CONFIRM"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_role_confirm_btn", language),
                    callback_data=callback_prefix,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_role_cancel_btn", language),
                    callback_data="SA_ROLE_CANCEL",
                )
            ],
        ]
    )

@admin_router.callback_query(
    F.data == "SA_ROLE_GRANT"
)
async def super_admin_role_grant_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get(
        "super_admin_selected_user_id"
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_super_admin_role_grant
    )
    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "super_admin_role_action_format",
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id,
    )

@admin_router.callback_query(
    F.data == "SA_ROLE_REVOKE"
)
async def super_admin_role_revoke_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get(
        "super_admin_selected_user_id"
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_super_admin_role_revoke
    )
    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "super_admin_role_action_format",
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id,
    )

@admin_router.message(
    AdminModerationFSM.entering_super_admin_role_grant
)
async def super_admin_role_grant_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    parsed = parse_super_admin_role_action(
        message.text
    )

    if not parsed:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('super_admin_role_bad_format', language)}\n\n"
                f"{t('super_admin_role_action_format', language)}"
            ),
        )
        return

    role, reason = parsed
    data = await state.get_data()
    target_user = (
        data.get(
            "super_admin_selected_user_id"
        )
        or "-"
    )

    await state.update_data(
        super_admin_role_action="grant",
        super_admin_role=role,
        super_admin_role_reason=reason,
    )

    danger = role == "super_admin"

    await state.set_state(
        AdminModerationFSM.confirming_super_admin_role_grant_final
        if danger
        else AdminModerationFSM.confirming_super_admin_role_grant
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "super_admin_role_grant_confirm",
            language,
        ).format(
            user=f"user-{target_user[:8]}",
            role=role,
            reason=reason,
        ),
        reply_markup=(
            super_admin_role_confirm_keyboard(
                "grant",
                danger=danger,
                language=language,
            )
        ),
    )


@admin_router.message(
    AdminModerationFSM.entering_super_admin_role_revoke
)
async def super_admin_role_revoke_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    parsed = parse_super_admin_role_action(
        message.text
    )

    if not parsed:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                f"{t('super_admin_role_bad_format', language)}\n\n"
                f"{t('super_admin_role_action_format', language)}"
            ),
        )
        return

    role, reason = parsed
    data = await state.get_data()
    target_user = (
        data.get(
            "super_admin_selected_user_id"
        )
        or "-"
    )

    await state.update_data(
        super_admin_role_action="revoke",
        super_admin_role=role,
        super_admin_role_reason=reason,
    )

    danger = role == "super_admin"

    await state.set_state(
        AdminModerationFSM.confirming_super_admin_role_revoke_final
        if danger
        else AdminModerationFSM.confirming_super_admin_role_revoke
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "super_admin_role_revoke_confirm",
            language,
        ).format(
            user=f"user-{target_user[:8]}",
            role=role,
            reason=reason,
        ),
        reply_markup=(
            super_admin_role_confirm_keyboard(
                "revoke",
                danger=danger,
                language=language,
            )
        ),
    )


@admin_router.callback_query(F.data == "SA_ROLE_CANCEL")
async def super_admin_role_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    await state.set_state(None)
    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "super_admin_role_cancelled",
            language,
        ),
        reply_markup=super_admin_user_card_keyboard(
            language
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id,
    )


@admin_router.callback_query(
    F.data.in_(
        {
            "SA_ROLE_GRANT_CONFIRM",
            "SA_ROLE_REVOKE_CONFIRM",
        }
    )
)
async def super_admin_role_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    await super_admin_role_execute(
        callback,
        state,
    )


@admin_router.callback_query(
    F.data.in_(
        {
            "SA_ROLE_GRANT_FINAL",
            "SA_ROLE_REVOKE_FINAL",
        }
    )
)
async def super_admin_role_final_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    role = data.get("super_admin_role")

    if role == "super_admin":
        await callback.answer()

        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "super_admin_role_danger_confirm",
                language,
            ),
            reply_markup=(
                super_admin_role_confirm_keyboard(
                    data.get(
                        "super_admin_role_action"
                    ),
                    danger=False,
                    language=language,
                )
            ),
        )

        await state.update_data(
            last_menu_message_id=(
                menu_message.message_id
            ),
        )
        return

    await super_admin_role_execute(
        callback,
        state,
    )

async def super_admin_role_execute(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    target_user_id_raw = data.get("super_admin_selected_user_id")
    action = data.get("super_admin_role_action")
    role = data.get("super_admin_role")
    reason = data.get("super_admin_role_reason")

    if not target_user_id_raw or action not in {"grant", "revoke"}:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(target_user_id_raw)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            service = ModerationService(ModerationRepository(session))

            if action == "grant":
                await service.grant_super_admin_user_role(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    target_user_id=target_user_id,
                    role=role,
                    reason=reason,
                )
            else:
                await service.revoke_super_admin_user_role(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    target_user_id=target_user_id,
                    role=role,
                    reason=reason,
                )

    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        super_admin_role_action=None,
        super_admin_role=None,
        super_admin_role_reason=None,
    )
    await state.set_state(None)
    await callback.answer()

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "super_admin_role_changed",
            language,
        ),
        reply_markup=super_admin_user_card_keyboard(
            language
        ),
    )

    await state.update_data(
        last_menu_message_id=(
            menu_message.message_id
        ),
    )

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
def format_super_admin_user_search_results(
    items,
    language: str,
) -> str:
    if not items:
        return t("super_admin_user_not_found", language)

    lines = [
        t("super_admin_user_search_header", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        roles = (
            ", ".join(
                super_admin_user_role_label(
                    role,
                    language,
                )
                for role in item.roles
            )
            if item.roles
            else "-"
        )
        lines.append(
            t("super_admin_user_search_card", language).format(
                number=index,
                name=item.display_name,
                user_number=item.user_number,
                telegram_id=item.telegram_id,
                username=item.username,
                status=super_admin_user_status_label(
                    item.status,
                    language,
                ),
                roles=roles,
            )
        )

    return "\n\n".join(lines)


def super_admin_user_search_keyboard(
    items,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, item in enumerate(items, start=1):
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{index}. "
                        f"{t('super_admin_user_profile_btn', language)}"
                    ),
                    callback_data=f"SA_USER_OPEN:{index - 1}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("super_admin_back_to_menu_btn", language),
                callback_data="ADM_PANEL",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="MAIN_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

@admin_router.callback_query(F.data == "SA_USERS")
async def super_admin_users_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await callback.answer()

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=t(
                "admin_access_denied",
                language,
            ),
        )

        await state.update_data(
            last_menu_message_id=menu_message.message_id,
        )
        return

    await state.set_state(
        AdminModerationFSM.waiting_super_admin_user_search
    )

    menu_message = await edit_or_replace_menu_message(
        callback=callback,
        text=t(
            "super_admin_user_search_prompt",
            language,
        ),
    )

    await state.update_data(
        last_menu_message_id=menu_message.message_id,
    )


@admin_router.message(
    AdminModerationFSM.waiting_super_admin_user_search
)
async def super_admin_user_search_message(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    query = (message.text or "").strip()

    if len(query) < 2:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "super_admin_user_search_too_short",
                language,
            ),
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            message.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
        )
        await state.set_state(None)
        return

    try:
        async with get_session() as session:
            items = await ModerationService(
                ModerationRepository(session)
            ).search_super_admin_users(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                query=query,
            )

    except ModerationError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=str(exc),
        )
        return

    await state.update_data(
        super_admin_user_search_ids=[
            str(item.user_id)
            for item in items
        ],
        super_admin_user_search_query=query,
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=format_super_admin_user_search_results(
            items,
            language,
        ),
        reply_markup=super_admin_user_search_keyboard(
            items,
            language,
        ),
    )

    await state.set_state(None)

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

def format_pending_profiles_header(
    *,
    page: int,
    count: int,
    language: str,
) -> str:
    return t("moderator_profiles_header", language).format(
        page=page + 1,
        count=count,
    )


def format_pending_profile_queue_item(
    item,
    *,
    number: int,
    language: str,
) -> str:
    city = item.city_name or t("moderator_city_not_set", language)
    created_at = (
        item.created_at.strftime("%Y-%m-%d")
        if item.created_at
        else "-"
    )

    return t("moderator_profile_queue_item", language).format(
        number=number,
        name=item.display_name,
        profession=item.profession_name,
        city=city,
        date=created_at,
    )


def pending_profile_queue_item_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("moderator_open_btn", language),
                    callback_data=f"ADM_SP_OPEN:{index}",
                )
            ]
        ]
    )


def pending_profiles_queue_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []
    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_SP_QUEUE:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_SP_QUEUE:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("moderator_back_btn", language),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def pending_specialist_keyboard(
    *,
    index: int,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
            [
                InlineKeyboardButton(
                    text=t("moderator_request_changes_btn", language),
                    callback_data=f"ADM_SP_CHANGES:{index}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_scoped_blacklist_btn", language),
                    callback_data=f"ADM_SP_SCOPED_BLOCK:{index}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_back_to_profiles_btn", language),
                    callback_data=f"ADM_SP_QUEUE:{page}",
                )
            ],
        ]
    )

def format_complaint_queue_item(
    card: ModeratorComplaintQueueCard,
    *,
    number: int,
    language: str,
) -> str:
    created_at = (
        card.created_at.strftime("%Y-%m-%d")
        if card.created_at
        else "-"
    )

    escalation = (
        f"\n{t('moderator_complaint_admin_target', language)}"
        if card.requires_admin_escalation
        else ""
    )

    return t(
        "moderator_complaint_queue_item",
        language,
    ).format(
        number=number,
        reporter=card.reporter_label,
        target=card.target_label,
        reason=card.reason,
        status=card.status,
        date=created_at,
        escalation=escalation,
    )


def complaint_queue_item_keyboard(
    *,
    index: int,
    can_take: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("moderator_open_btn", language),
                callback_data=f"ADM_CP_VIEW:{index}",
            )
        ]
    ]

    if can_take:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_take_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_TAKE:{index}",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def complaints_queue_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("moderator_complaint_filter_btn", language),
                callback_data="ADM_CP_FILTER",
            )
        ]
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_CP_QUEUE:{view}:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_CP_QUEUE:{view}:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("moderator_back_btn", language),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def complaints_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_open", language),
                    callback_data="ADM_CP_QUEUE:open:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_new", language),
                    callback_data="ADM_CP_QUEUE:new:0",
                ),
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_review", language),
                    callback_data="ADM_CP_QUEUE:in_review:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_resolved", language),
                    callback_data="ADM_CP_QUEUE:resolved:0",
                ),
                InlineKeyboardButton(
                    text=t("moderator_complaint_filter_rejected", language),
                    callback_data="ADM_CP_QUEUE:rejected:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("moderator_back_btn", language),
                    callback_data="ADM_COMPLAINTS",
                )
            ],
        ]
    )

def format_global_blacklist_card(
    card: AdminGlobalBlacklistCard,
    *,
    number: int,
    language: str,
) -> str:
    comment = (
        card.comment
        or t("admin_global_blacklist_no_comment", language)
    )

    return t(
        "admin_global_blacklist_card",
        language,
    ).format(
        number=number,
        user=card.user_label,
        reason=card.reason,
        comment=comment,
        status=card.status,
        actor=card.actor_label,
        date=card.created_at.strftime("%Y-%m-%d"),
    )


def global_blacklist_card_keyboard(
    *,
    index: int,
    can_revoke: bool,
    language: str,
) -> InlineKeyboardMarkup | None:
    if not can_revoke:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_global_blacklist_revoke_btn",
                        language,
                    ),
                    callback_data=f"ADM_USER_GLOBAL_UNBLOCK:{index}",
                )
            ]
        ]
    )

def super_admin_global_blacklist_card_keyboard(
    *,
    index: int,
    can_revoke: bool,
    language: str,
) -> InlineKeyboardMarkup | None:
    if not can_revoke:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_global_blacklist_revoke_btn",
                        language,
                    ),
                    callback_data=f"SA_GBL_REVOKE:{index}",
                )
            ]
        ]
    )

def global_blacklist_queue_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_active_btn",
                    language,
                ),
                callback_data="ADM_GBL_QUEUE:active:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_history_btn",
                    language,
                ),
                callback_data="ADM_GBL_QUEUE:history:0",
            ),
        ]
    ]

    if view == "active":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_global_blacklist_add_btn",
                        language,
                    ),
                        callback_data="ADM_USERS",
                )
            ]
        )

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"ADM_GBL_QUEUE:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"ADM_GBL_QUEUE:{view}:{page + 1}"
                ),
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

def super_admin_global_blacklist_queue_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_active_btn",
                    language,
                ),
                callback_data="SA_GBL_QUEUE:active:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_history_btn",
                    language,
                ),
                callback_data="SA_GBL_QUEUE:history:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_add_btn",
                    language,
                ),
                callback_data="SA_GBL_ADD",
            )
        ],
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_GBL_QUEUE:{view}:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_GBL_QUEUE:{view}:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="SA_PANEL",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("main_menu", language),
                callback_data="MAIN_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

@admin_router.callback_query(F.data == "SA_GLOBAL_BLACKLIST")
async def open_super_admin_global_blacklist(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_super_admin_global_blacklist_queue(
        callback,
        state,
        view="active",
        page=0,
    )



@admin_router.callback_query(F.data.startswith("SA_GBL_QUEUE:"))
async def change_super_admin_global_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    view = (
        parts[1]
        if len(parts) > 1 and parts[1] in {"active", "history"}
        else "active"
    )

    try:
        page = max(0, int(parts[2]))
    except (IndexError, TypeError, ValueError):
        page = 0

    await open_super_admin_global_blacklist_queue(
        callback,
        state,
        view=view,
        page=page,
    )

@admin_router.callback_query(F.data == "SA_GBL_ADD")
async def ask_super_admin_global_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .entering_super_admin_global_blacklist_add
    )
    await state.update_data(
        super_admin_global_blacklist_add_user_id=None,
        super_admin_global_blacklist_add_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=(
            "Введите пользователя и причину одним сообщением.\n\n"
            "Формат:\n"
            "user-49ba690f причина блокировки\n\n"
            "Можно указать user-facing ID, Telegram ID или username.\n"
            "Причина минимум 3 символа."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data="SA_GBL_ADD_CANCEL",
                    )
                ]
            ]
        ),
    )


@admin_router.message(
    AdminModerationFSM.entering_super_admin_global_blacklist_add
)
async def receive_super_admin_global_blacklist_add(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )

    raw_text = (message.text or "").strip()
    parts = raw_text.split(maxsplit=1)

    if len(parts) != 2:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                "Неверный формат.\n\n"
                "Пример:\n"
                "user-49ba690f test global block"
            ),
        )
        return

    query, reason = (
        parts[0].strip(),
        parts[1].strip(),
    )

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_reason_too_short",
                language,
            ),
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            message.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
        )
        return

    try:
        async with get_session() as session:
            matches = await ModerationService(
                ModerationRepository(session)
            ).search_super_admin_users(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                query=query,
            )
    except ModerationError as exc:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=str(exc),
        )
        return

    if not matches:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_user_not_found",
                language,
            ),
        )
        return

    if len(matches) > 1:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=(
                "Найдено несколько пользователей. "
                "Уточните user-facing ID, Telegram ID или username."
            ),
        )
        return

    target = matches[0]

    await state.update_data(
        super_admin_global_blacklist_add_user_id=str(
            target.user_id
        ),
        super_admin_global_blacklist_add_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM
        .confirming_super_admin_global_blacklist_add
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_user_global_block_confirmation",
            language,
        ).format(
            user_number=target.user_number,
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_block_confirm_btn",
                            language,
                        ),
                        callback_data="SA_GBL_ADD_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data="SA_GBL_ADD",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data="SA_GBL_ADD_CANCEL",
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(
    AdminModerationFSM.confirming_super_admin_global_blacklist_add,
    F.data == "SA_GBL_ADD_CONFIRM",
)
async def confirm_super_admin_global_blacklist_add_first(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    target_user_id = data.get(
        "super_admin_global_blacklist_add_user_id"
    )
    reason = data.get(
        "super_admin_global_blacklist_add_reason"
    )

    if not target_user_id or not reason:
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_id = UUID(target_user_id)
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .confirming_super_admin_global_blacklist_add_final
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_block_final_confirmation",
            language,
        ).format(
            user_number=f"user-{target_id.hex[:8]}",
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_block_final_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_ADD_FINAL_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data="SA_GBL_ADD",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data="SA_GBL_ADD_CANCEL",
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(
    AdminModerationFSM
    .confirming_super_admin_global_blacklist_add_final,
    F.data == "SA_GBL_ADD_FINAL_CONFIRM",
)
async def execute_super_admin_global_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    target_user_id = data.get(
        "super_admin_global_blacklist_add_user_id"
    )
    reason = data.get(
        "super_admin_global_blacklist_add_reason"
    )

    try:
        target_id = UUID(str(target_user_id))
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.set_state(None)
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).block_user(
                admin_user_id=admin_user_id,
                user_id=target_id,
                reason=reason,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await state.update_data(
        super_admin_global_blacklist_add_user_id=None,
        super_admin_global_blacklist_add_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_block_completed",
            language,
        ).format(
            status=result.status,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_global_blacklist_btn",
                            language,
                        ).format(
                            count=0
                        ),
                        callback_data="SA_GLOBAL_BLACKLIST",
                    )
                ]
            ]
        ),
    )


@admin_router.callback_query(F.data == "SA_GBL_ADD_CANCEL")
async def cancel_super_admin_global_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.set_state(None)
    await state.update_data(
        super_admin_global_blacklist_add_user_id=None,
        super_admin_global_blacklist_add_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_block_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_global_blacklist_btn",
                            language,
                        ).format(
                            count=0
                        ),
                        callback_data="SA_GLOBAL_BLACKLIST",
                    )
                ]
            ]
        ),
    )

async def open_super_admin_global_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    (
        admin_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).open_super_admin_global_blacklist_queue(
                admin_user_id=admin_user_id,
                view=view,
                page=page,
                page_size=ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    state_data = await state.get_data()

    await state.update_data(
        super_admin_global_blacklist_ids=[
            str(card.blacklist_id)
            for card in result.items
        ],
        super_admin_global_blacklist_user_ids=[
            str(card.user_id)
            for card in result.items
        ],
        super_admin_global_blacklist_can_revoke=[
            card.can_revoke
            for card in result.items
        ],
        super_admin_global_blacklist_view=result.view,
        super_admin_global_blacklist_page=result.page,
    )

    view_label = t(
        (
            "admin_global_blacklist_history_title"
            if result.view == "history"
            else "admin_global_blacklist_active_title"
        ),
        language,
    )

    await callback.answer()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            state_data.get(
                "last_menu_message_id"
            ),
            *(
                state_data.get(
                    "admin_global_blacklist_message_ids"
                )
                or []
            ),
        ],
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "admin_global_blacklist_queue_title",
            language,
        ).format(
            view=view_label,
            count=len(result.items),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not result.items:
        empty_message = await callback.message.answer(
            t(
                "admin_global_blacklist_empty",
                language,
            ),
            reply_markup=(
                super_admin_global_blacklist_queue_keyboard(
                    view=result.view,
                    page=result.page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            admin_global_blacklist_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        return

    start_number = (
        result.page
        * ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE
        + 1
    )

    for offset, card in enumerate(result.items):
        card_message = await callback.message.answer(
            format_global_blacklist_card(
                card,
                number=start_number + offset,
                language=language,
            ),
            reply_markup=(
                super_admin_global_blacklist_card_keyboard(
                    index=offset,
                    can_revoke=card.can_revoke,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "admin_global_blacklist_actions_title",
            language,
        ),
        reply_markup=(
            super_admin_global_blacklist_queue_keyboard(
                view=result.view,
                page=result.page,
                has_next=result.has_next,
                language=language,
            )
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        admin_global_blacklist_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

def format_admin_audit_card(
    card: AdminAuditCard,
    *,
    number: int,
    language: str,
) -> str:
    return t(
        "admin_audit_card",
        language,
    ).format(
        number=number,
        date=card.date,
        actor=card.actor,
        action=card.action,
        target=card.target,
        reason=card.reason,
        source=card.source,
    )

def admin_audit_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_open_btn", language),
                    callback_data=f"ADM_AUDIT_OPEN:{index}",
                )
            ]
        ]
    )

def super_admin_audit_card_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_open_btn", language),
                    callback_data=f"SA_AUDIT_OPEN:{index}",
                )
            ]
        ]
    )

def super_admin_system_value_label(
    value: str | None,
    language: str,
) -> str:
    normalized = (value or "").strip().lower()

    key_by_value = {
        "unknown": "super_admin_system_value_unknown",
        "ok": "super_admin_system_value_ok",
        "error": "super_admin_system_value_error",
        "configured": "super_admin_system_value_configured",
        "not configured": (
            "super_admin_system_value_not_configured"
        ),
        "enabled": "super_admin_system_value_enabled",
        "disabled": "super_admin_system_value_disabled",
        "available: yes; secrets hidden": (
            "super_admin_system_value_env_available"
        ),
    }

    key = key_by_value.get(normalized)
    return (
        t(key, language)
        if key
        else str(value or "—")
    )



def format_super_admin_system_status(
    card,
    language: str,
) -> str:
    return t("super_admin_system_status", language).format(
        app_version=super_admin_system_value_label(
            card.app_version,
            language,
        ),
        db_status=super_admin_system_value_label(
            card.db_status,
            language,
        ),
        db_version=card.db_version,
        telegram_status=super_admin_system_value_label(
            card.telegram_status,
            language,
        ),
        migration_version=card.migration_version,
        migrations_status=super_admin_system_value_label(
            card.migrations_status,
            language,
        ),
        maintenance_mode=super_admin_system_value_label(
            card.maintenance_mode,
            language,
        ),
        feature_flags_status=super_admin_system_value_label(
            card.feature_flags_status,
            language,
        ),
        env_status=super_admin_system_value_label(
            card.env_status,
            language,
        ),
    )


def super_admin_system_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_feature_flags_btn", language),
                    callback_data="SA_SYSTEM_FEATURE_FLAGS",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_health_check_btn", language),
                    callback_data="SA_SYSTEM_HEALTH",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_maintenance_btn", language),
                    callback_data="SA_SYSTEM_MAINTENANCE",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_migrations_btn", language),
                    callback_data="SA_SYSTEM_MIGRATIONS",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_env_status_btn", language),
                    callback_data="SA_SYSTEM_ENV",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

def format_super_admin_smoke_tests(
    items,
    language: str,
) -> str:
    if not items:
        return t("super_admin_smoke_empty", language)

    lines = [
        t("super_admin_smoke_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("super_admin_smoke_card", language).format(
                number=index,
                code=item.code,
                title=item.title,
                status=item.status,
                detail=item.detail,
            )
        )

    return "\n\n".join(lines)


def format_super_admin_smoke_run(
    result,
    language: str,
) -> str:
    lines = [
        t("super_admin_smoke_result_title", language).format(
            total=result.total,
            passed=result.passed,
            failed=result.failed,
        )
    ]

    for index, item in enumerate(result.results, start=1):
        lines.append(
            t("super_admin_smoke_result_card", language).format(
                number=index,
                code=item.code,
                title=item.title,
                status=item.status,
                detail=item.detail,
            )
        )

    return "\n\n".join(lines)

def format_super_admin_smoke_history(
    items,
    language: str,
) -> str:
    if not items:
        return t("super_admin_smoke_history_empty", language)

    lines = [
        t("super_admin_smoke_history_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("super_admin_smoke_history_card", language).format(
                number=index,
                date=item.date,
                selected_code=item.selected_code,
                total=item.total,
                passed=item.passed,
                failed=item.failed,
                destructive=item.destructive,
            )
        )

    return "\n\n".join(lines)


def super_admin_smoke_selected_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Start",
                    callback_data="SA_SMOKE_RUN:start",
                ),
                InlineKeyboardButton(
                    text="Registration",
                    callback_data="SA_SMOKE_RUN:registration",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Search",
                    callback_data="SA_SMOKE_RUN:search",
                ),
                InlineKeyboardButton(
                    text="Request",
                    callback_data="SA_SMOKE_RUN:request",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Dialogs",
                    callback_data="SA_SMOKE_RUN:dialogs",
                ),
                InlineKeyboardButton(
                    text="Support",
                    callback_data="SA_SMOKE_RUN:support",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Moderation",
                    callback_data="SA_SMOKE_RUN:moderation",
                ),
                InlineKeyboardButton(
                    text="Admin access",
                    callback_data="SA_SMOKE_RUN:admin_access",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="SA_SMOKE",
                )
            ],
        ]
    )

def super_admin_smoke_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_smoke_run_all_btn", language),
                    callback_data="SA_SMOKE_RUN_ALL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_smoke_run_selected_btn", language),
                    callback_data="SA_SMOKE_RUN_SELECTED",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_smoke_history_btn", language),
                    callback_data="SA_SMOKE_HISTORY",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

@admin_router.callback_query(F.data == "SA_SMOKE")
async def super_admin_smoke_panel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    async with get_session() as session:
        items = ModerationService(
            ModerationRepository(session)
        ).list_super_admin_smoke_definitions()

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_smoke_tests(
            items,
            language,
        ),
        reply_markup=super_admin_smoke_keyboard(
            language
        ),
    )

@admin_router.callback_query(
    F.data == "SA_SMOKE_RUN_ALL"
)
async def super_admin_smoke_run_all(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_smoke_progress",
            language,
        ),
    )

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).run_super_admin_smoke_tests(
                admin_user_id=admin_user_id,
            )

    except ModerationError as exc:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=str(exc),
            reply_markup=super_admin_smoke_keyboard(
                language
            ),
            callback_answered=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_smoke_run(
            result,
            language,
        ),
        reply_markup=super_admin_smoke_keyboard(
            language
        ),
        callback_answered=True,
    )

@admin_router.callback_query(
    F.data == "SA_SMOKE_RUN_SELECTED"
)
async def super_admin_smoke_select(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_smoke_select_title",
            language,
        ),
        reply_markup=(
            super_admin_smoke_selected_keyboard(
                language
            )
        ),
    )


@admin_router.callback_query(
    F.data.startswith("SA_SMOKE_RUN:")
)
async def super_admin_smoke_run_selected(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    selected_code = (
        callback.data or ""
    ).split(":", 1)[1]

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_smoke_progress",
            language,
        ),
    )

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).run_super_admin_smoke_tests(
                admin_user_id=admin_user_id,
                selected_code=selected_code,
            )

    except ModerationError as exc:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=str(exc),
            reply_markup=super_admin_smoke_keyboard(
                language
            ),
            callback_answered=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_smoke_run(
            result,
            language,
        ),
        reply_markup=super_admin_smoke_keyboard(
            language
        ),
        callback_answered=True,
    )


@admin_router.callback_query(
    F.data == "SA_SMOKE_HISTORY"
)
async def super_admin_smoke_history(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await ModerationService(
                ModerationRepository(session)
            ).list_super_admin_smoke_history(
                admin_user_id=admin_user_id,
            )

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_smoke_history(
            items,
            language,
        ),
        reply_markup=super_admin_smoke_keyboard(
            language
        ),
    )

@admin_router.callback_query(
    F.data == "SA_SYSTEM"
)
async def super_admin_system_panel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).open_super_admin_system_status(
                admin_user_id=admin_user_id,
            )

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_system_status(
            card,
            language,
        ),
        reply_markup=super_admin_system_keyboard(
            language
        ),
    )

@admin_router.callback_query(F.data.startswith("SA_SYSTEM_"))
async def super_admin_system_detail(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).open_super_admin_system_status(
                admin_user_id=admin_user_id,
            )

    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    detail_type = (callback.data or "").replace("SA_SYSTEM_", "")

    if detail_type == "HEALTH":
        text = t(
            "super_admin_system_health_detail",
            language,
        ).format(
            db_status=super_admin_system_value_label(
                card.db_status,
                language,
            ),
            telegram_status=super_admin_system_value_label(
                card.telegram_status,
                language,
            ),
            maintenance_mode=super_admin_system_value_label(
                card.maintenance_mode,
                language,
            ),
        )
    elif detail_type == "MIGRATIONS":
        text = t(
            "super_admin_system_migrations_detail",
            language,
        ).format(
            migrations_status=super_admin_system_value_label(
                card.migrations_status,
                language,
            ),
            migration_version=card.migration_version,
        )
    elif detail_type == "ENV":
        text = t(
            "super_admin_system_env_detail",
            language,
        ).format(
            env_status=super_admin_system_value_label(
                card.env_status,
                language,
            ),
        )
    elif detail_type == "FEATURE_FLAGS":
        text = t(
            "super_admin_system_feature_flags_detail",
            language,
        ).format(
            feature_flags_status=super_admin_system_value_label(
                card.feature_flags_status,
                language,
            ),
        )
    elif detail_type == "MAINTENANCE":
        text = t(
            "super_admin_system_maintenance_detail",
            language,
        ).format(
            maintenance_mode=super_admin_system_value_label(
                card.maintenance_mode,
                language,
            ),
        )
    else:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_back_to_menu_btn",
                            language,
                        ),
                        callback_data="SA_SYSTEM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_menu",
                            language,
                        ),
                        callback_data="MAIN_MENU",
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(F.data.startswith("SA_GBL_REVOKE:"))
async def ask_super_admin_global_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("super_admin_global_blacklist_user_ids") or []
    can_revoke = data.get("super_admin_global_blacklist_can_revoke") or []

    if (
        index < 0
        or index >= len(user_ids)
        or index >= len(can_revoke)
        or not can_revoke[index]
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .entering_super_admin_global_blacklist_revoke
    )
    await state.update_data(
        super_admin_global_blacklist_revoke_index=index,
        super_admin_global_blacklist_revoke_reason=None,
    )

    await callback.answer()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=(
            data.get(
                "admin_global_blacklist_message_ids"
            )
            or []
        ),
    )

    await state.update_data(
        admin_global_blacklist_message_ids=[],
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_unblock_reason_prompt",
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
                        callback_data="SA_GBL_REVOKE_CANCEL",
                    )
                ]
            ]
        ),
        callback_answered=True,
    )

@admin_router.message(
    AdminModerationFSM.entering_super_admin_global_blacklist_revoke
)
async def receive_super_admin_global_blacklist_revoke_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_reason_too_short",
                language,
            ),
        )
        return

    data = await state.get_data()
    index = data.get(
        "super_admin_global_blacklist_revoke_index"
    )
    user_ids = (
        data.get(
            "super_admin_global_blacklist_user_ids"
        )
        or []
    )

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
    ):
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_user_not_found",
                language,
            ),
        )
        return

    try:
        target_user_id = UUID(
            str(user_ids[index])
        )
    except (TypeError, ValueError):
        await state.set_state(None)

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_user_not_found",
                language,
            ),
        )
        return

    await state.update_data(
        super_admin_global_blacklist_revoke_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM
        .confirming_super_admin_global_blacklist_revoke
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "admin_user_global_unblock_confirmation",
            language,
        ).format(
            user_number=(
                f"user-{target_user_id.hex[:8]}"
            ),
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_unblock_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_REVOKE_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_GBL_REVOKE:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_REVOKE_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    AdminModerationFSM
    .confirming_super_admin_global_blacklist_revoke,
    F.data == "SA_GBL_REVOKE_CONFIRM",
)
async def confirm_super_admin_global_blacklist_revoke_first(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("super_admin_global_blacklist_revoke_index")
    reason = data.get("super_admin_global_blacklist_revoke_reason")
    user_ids = data.get("super_admin_global_blacklist_user_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
        or not reason
    ):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .confirming_super_admin_global_blacklist_revoke_final
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_unblock_final_confirmation",
            language,
        ).format(
            user_number=(
                f"user-{target_user_id.hex[:8]}"
            ),
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_unblock_final_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_REVOKE_FINAL_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_GBL_REVOKE:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "cancel",
                            language,
                        ),
                        callback_data=(
                            "SA_GBL_REVOKE_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(
    AdminModerationFSM
    .confirming_super_admin_global_blacklist_revoke_final,
    F.data == "SA_GBL_REVOKE_FINAL_CONFIRM",
)
async def execute_super_admin_global_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("super_admin_global_blacklist_revoke_index")
    reason = data.get("super_admin_global_blacklist_revoke_reason")
    user_ids = data.get("super_admin_global_blacklist_user_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
    ):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.set_state(None)
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).unblock_user(
                admin_user_id=admin_user_id,
                user_id=target_user_id,
                reason=reason,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await state.update_data(
        super_admin_global_blacklist_revoke_index=None,
        super_admin_global_blacklist_revoke_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_unblock_completed",
            language,
        ).format(
            status=result.status,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_global_blacklist_btn",
                            language,
                        ).format(
                            count=0
                        ),
                        callback_data="SA_GLOBAL_BLACKLIST",
                    )
                ]
            ]
        ),
    )


@admin_router.callback_query(F.data == "SA_GBL_REVOKE_CANCEL")
async def cancel_super_admin_global_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.set_state(None)
    await state.update_data(
        super_admin_global_blacklist_revoke_index=None,
        super_admin_global_blacklist_revoke_reason=None,
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_user_global_unblock_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_global_blacklist_btn",
                            language,
                        ).format(
                            count=0
                        ),
                        callback_data="SA_GLOBAL_BLACKLIST",
                    )
                ]
            ]
        ),
    )

def admin_audit_details_keyboard(
    *,
    target_type: str,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_back_to_list_btn", language),
                    callback_data=(
                        f"ADM_AUDIT_QUEUE:{target_type}:{page}"
                    ),
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

def admin_audit_queue_keyboard(
    *,
    target_type: str,
    page: int,
    has_next: bool,
    language: str,
    prefix: str = "ADM_AUDIT",
    back_callback: str = "ADM_PANEL",
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_audit_filter_btn", language),
                callback_data=f"{prefix}_FILTER",
            )
        ]
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"{prefix}_QUEUE:{target_type}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"{prefix}_QUEUE:{target_type}:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data=back_callback,
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_audit_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_all", language),
                    callback_data="ADM_AUDIT_QUEUE:all:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_users", language),
                    callback_data="ADM_AUDIT_QUEUE:user:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_specialists", language),
                    callback_data="ADM_AUDIT_QUEUE:specialist:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_support", language),
                    callback_data="ADM_AUDIT_QUEUE:support_ticket:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_complaints", language),
                    callback_data="ADM_AUDIT_QUEUE:complaint:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_reviews", language),
                    callback_data="ADM_AUDIT_QUEUE:review:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_portfolio", language),
                    callback_data=(
                        "ADM_AUDIT_QUEUE:"
                        "specialist_portfolio_item:0"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_blacklist", language),
                    callback_data="ADM_AUDIT_QUEUE:blacklist:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_LOGS",
                )
            ],
        ]
    )

def super_admin_audit_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_all", language),
                    callback_data="SA_AUDIT_QUEUE:all:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_users", language),
                    callback_data="SA_AUDIT_QUEUE:user:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_specialists", language),
                    callback_data="SA_AUDIT_QUEUE:specialist:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_support", language),
                    callback_data="SA_AUDIT_QUEUE:support_ticket:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_complaints", language),
                    callback_data="SA_AUDIT_QUEUE:complaint:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_reviews", language),
                    callback_data="SA_AUDIT_QUEUE:review:0",
                ),
                InlineKeyboardButton(
                    text=t("admin_audit_filter_portfolio", language),
                    callback_data="SA_AUDIT_QUEUE:specialist_portfolio_item:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_blacklist", language),
                    callback_data="SA_AUDIT_QUEUE:blacklist:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                )
            ],
        ]
    )

def format_scoped_blacklist_card(
    card: ModeratorScopedBlacklistCard,
    *,
    number: int,
    language: str,
) -> str:
    comment = (
        card.comment
        or t(
            "moderator_blacklist_no_comment",
            language,
        )
    )

    revoke_reason = (
        card.revoke_reason
        or t(
            "moderator_blacklist_no_revoke_reason",
            language,
        )
    )

    revoke_line = ""

    if card.status == "revoked":
        revoke_line = (
            "\n"
            + t(
                "moderator_blacklist_revoke_reason_line",
                language,
            ).format(reason=revoke_reason)
        )

    return t(
        "moderator_blacklist_card",
        language,
    ).format(
        number=number,
        user=card.user_label,
        scope=card.scope_label,
        reason=card.reason,
        comment=comment,
        status=card.status,
        date=card.created_at.strftime("%Y-%m-%d"),
        revoke_line=revoke_line,
    )

def scoped_blacklist_card_keyboard(
    *,
    index: int,
    can_revoke: bool,
    language: str,
) -> InlineKeyboardMarkup | None:
    if not can_revoke:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_blacklist_revoke_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_BL_REVOKE:{index}"
                    ),
                )
            ]
        ]
    )

def scoped_blacklist_queue_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_blacklist_active_btn",
                    language,
                ),
                callback_data="ADM_BL_QUEUE:active:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "moderator_blacklist_history_btn",
                    language,
                ),
                callback_data="ADM_BL_QUEUE:revoked:0",
            ),
        ]
    ]

    if view == "active":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_blacklist_add_btn",
                        language,
                    ),
                    callback_data="ADM_BL_ADD",
                )
            ]
        )

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"ADM_BL_QUEUE:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"ADM_BL_QUEUE:{view}:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_back_btn",
                    language,
                ),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )

@admin_router.callback_query(
    F.data == "ADM_BL_ADD"
)
async def ask_blacklist_add_user(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    (
        moderator_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_blacklist_add_tenant_id=(
            str(tenant_id)
        ),
    )
    await state.set_state(
        AdminModerationFSM
        .entering_blacklist_add_user
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_add_user_prompt",
            language,
        ),
    )

@admin_router.message(
    AdminModerationFSM.entering_blacklist_add_user
)
async def receive_blacklist_add_user(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    telegram_id = (message.text or "").strip()

    if not telegram_id.isdigit():
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "moderator_blacklist_invalid_user",
                language,
            ),
        )
        return

    await state.update_data(
        moderator_blacklist_add_telegram_id=(
            telegram_id
        ),
    )
    await state.set_state(
        AdminModerationFSM
        .entering_blacklist_add_reason
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_scoped_block_reason_prompt",
            language,
        ),
    )


@admin_router.callback_query(
    F.data == "ADM_BL_ADD_EDIT_REASON"
)
async def edit_blacklist_add_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get(
        "moderator_blacklist_add_telegram_id"
    ):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .entering_blacklist_add_reason
    )
    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_reason_prompt",
            language,
        ),
    )


@admin_router.callback_query(
    F.data == "ADM_BL_ADD_CANCEL"
)
async def cancel_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.clear()

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_add_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_active_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_QUEUE:active:0"
                        ),
                    )
                ]
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_BL_ADD_CONFIRM"
)
async def confirm_blacklist_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    telegram_id = (
        data.get(
            "moderator_blacklist_add_telegram_id"
        )
        or ""
    ).strip()
    reason = (
        data.get(
            "moderator_blacklist_add_reason"
        )
        or ""
    ).strip()

    if not telegram_id.isdigit() or len(reason) < 3:
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    (
        moderator_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).add_scoped_blacklist_by_telegram_id(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                telegram_id=telegram_id,
                reason=reason,
            )

    except (ModerationError, ValueError) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "scoped_blacklist_added_manually "
        "telegram_id=%s target_telegram_id=%s "
        "blacklist_id=%s",
        callback.from_user.id,
        telegram_id,
        result.entity_id,
    )

    await state.clear()

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_scoped_block_created",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_active_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_QUEUE:active:0"
                        ),
                    )
                ]
            ]
        ),
    )

@admin_router.message(
    AdminModerationFSM.entering_blacklist_add_reason
)
async def receive_blacklist_add_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_reason_too_short",
                language,
            ),
        )
        return

    telegram_id = data.get(
        "moderator_blacklist_add_telegram_id"
    )

    if not telegram_id:
        await state.clear()

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
        )
        return

    await state.update_data(
        moderator_blacklist_add_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_blacklist_add
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_blacklist_add_confirmation",
            language,
        ).format(
            telegram_id=telegram_id,
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_add_confirm_btn",
                            language,
                        ),
                        callback_data="ADM_BL_ADD_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_ADD_EDIT_REASON"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data="ADM_BL_ADD_CANCEL",
                    )
                ],
            ]
        ),
    )

def complaint_keyboard(
    *,
    index: int,
    total: int,
    status: str,
    requires_admin_escalation: bool,
    view: str,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    if status == "in_review":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_resolve_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_RESOLVE:{index}",
                ),
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_reject_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_REJECT:{index}",
                ),
            ]
        )

    elif status == "new":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_reject_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_REJECT:{index}",
                )
            ]
        )

    if (
        status in {"new", "in_review"}
        and not requires_admin_escalation
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_scoped_blacklist_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_CP_SCOPED_BLOCK:{index}"
                    ),
                )
            ]
        )

    if status in {"new", "in_review"}:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_complaint_admin_btn",
                        language,
                    ),
                    callback_data=f"ADM_CP_ADMIN:{index}",
                )
            ]
        )

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
                text=t(
                    "moderator_complaint_back_queue_btn",
                    language,
                ),
                callback_data=(
                    f"ADM_CP_QUEUE:{view}:{page}"
                ),
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )

def review_keyboard(
    *,
    index: int,
    total: int,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_show_review", language),
                callback_data=f"ADM_RV_APPROVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_hide_review", language),
                callback_data=f"ADM_RV_HIDE:{index}",
            ),
        ]
    ]

    navigation = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_RV_VIEW:{index - 1}",
            )
        )
    elif page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_REVIEWS_PAGE:{page - 1}",
            )
        )

    if index + 1 < total:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_RV_VIEW:{index + 1}",
            )
        )
    elif has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_REVIEWS_PAGE:{page + 1}",
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

def portfolio_moderation_keyboard(
    *,
    index: int,
    total: int,
    page: int,
    has_next_page: bool,
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

    page_navigation = []

    if page > 0 and index == 0:
        page_navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_PORT_QUEUE:{page - 1}",
            )
        )

    if has_next_page and index == total - 1:
        page_navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PORT_QUEUE:{page + 1}",
            )
        )

    if page_navigation:
        rows.append(page_navigation)

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
    card,
    *,
    language: str,
) -> str:
    city = card.city_name or t("moderator_city_not_set", language)

    services = (
        "\n".join(f"- {title}" for title in card.service_titles)
        if card.service_titles
        else t("moderator_no_services", language)
    )

    return t("moderator_profile_card", language).format(
        name=card.display_name,
        profession=card.profession_name,
        city=city,
        status=card.status,
        description=card.description,
        contact=card.masked_contact,
        complaints=card.complaints_count,
        risk_flags=card.open_risk_flags_count,
        services=services,
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
    card: PendingManualPaymentCard,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    invoice_status = (
        card.invoice_status
        or t("admin_item_not_found", language)
    )

    return (
        f"{t('admin_pending_payment_title', language).format(index=index + 1, total=total)}\n\n"
        f"{t('billing_invoice_id', language)}: {card.invoice_id}\n"
        f"{t('billing_amount', language)}: "
        f"{card.amount} {card.currency}\n"
        f"{t('admin_status', language)}: "
        f"{card.payment_status}\n"
        f"{t('admin_invoice_status', language)}: "
        f"{invoice_status}\n"
        f"{t('billing_payment_method', language)}: "
        f"{card.payment_method}"
    )

def format_complaint_card(
    card: ModeratorComplaintCard,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    comment = (
        card.comment
        or t("admin_no_comment", language)
    )

    history = (
        "\n".join(card.history)
        if card.history
        else t(
            "moderator_complaint_history_empty",
            language,
        )
    )

    escalation = ""

    if card.requires_admin_escalation:
        escalation = (
            "\n\n"
            + t(
                "moderator_complaint_admin_target",
                language,
            )
        )

    return t(
        "moderator_complaint_card",
        language,
    ).format(
        index=index + 1,
        total=total,
        reporter=card.reporter_label,
        target=card.target_label,
        target_type=card.target_type,
        status=card.status,
        reason=card.reason,
        comment=comment,
        date=card.created_at.strftime("%Y-%m-%d"),
        history=history,
        escalation=escalation,
    )
def format_review_card(
    card: ReviewModerationCard,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    review = card.review
    review_text = (
        review.text
        or t("admin_no_comment", language)
    )
    target_name = (
        card.target_name
        or t("admin_review_target_unavailable", language)
    )

    return (
        f"{t('admin_review_title', language).format(index=index + 1, total=total)}\n\n"
        f"{t('admin_review_rating', language)}: {review.rating}/5\n"
        f"{t('admin_review_author', language)}: {card.author_label}\n"
        f"{t('admin_review_target', language)}: {target_name}\n\n"
        f"{t('admin_review_text', language)}:\n"
        f"{review_text}"
    )

def support_staff_menu_keyboard(
    language: str,
    *,
    show_role_switch: bool = True,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("support_staff_open_btn", language),
                callback_data="ADM_SUPPORT_VIEW:open:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("support_staff_in_progress_btn", language),
                callback_data="ADM_SUPPORT_VIEW:in_progress:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("support_staff_resolved_btn", language),
                callback_data="ADM_SUPPORT_VIEW:resolved:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("support_staff_search_btn", language),
                callback_data="ADM_SUPPORT_SEARCH",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("support_staff_stats_btn", language),
                callback_data="ADM_SUPPORT_STATS",
            )
        ],
    ]

    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
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


def format_support_staff_menu(counts: dict[str, int], language: str) -> str:
    return t("support_staff_menu_title", language).format(
        open_count=counts.get("open", 0),
        in_progress_count=counts.get("in_progress", 0),
        resolved_count=counts.get("resolved", 0),
    )

def support_staff_status_filter(view: str) -> set[str]:
    return {
        "open": {"open"},
        "in_progress": {"in_progress"},
        "resolved": {"resolved"},
    }.get(view, {"open"})


def support_staff_view_label(view: str, language: str) -> str:
    key = {
        "open": "support_staff_open_btn",
        "in_progress": "support_staff_in_progress_btn",
        "resolved": "support_staff_resolved_btn",
    }.get(view, "support_staff_open_btn")
    return t(key, language)


def format_support_staff_ticket_header(
    tickets,
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> str:
    view_label = support_staff_view_label(view, language)
    start = page * SUPPORT_STAFF_PAGE_SIZE + 1
    end = start + len(tickets) - 1

    if not tickets:
        return (
            f"{t('support_staff_list_title', language)}\n"
            f"{t('support_staff_empty_list', language).format(view=view_label)}"
        )

    return (
        f"{t('support_staff_list_title', language)}\n"
        f"{view_label}\n"
        f"{t('support_staff_shown_range', language).format(start=start, end=end)}"
    )

def format_support_staff_search_header(
    tickets,
    *,
    query: str,
    language: str,
) -> str:
    if not tickets:
        return t("support_staff_search_empty", language).format(query=query)

    return t("support_staff_search_results", language).format(
        query=query,
        count=len(tickets),
    )

def admin_escalated_ticket_card_keyboard(
    *,
    index: int,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_support_assign", language),
                    callback_data=(
                        f"ADM_ADMIN_TICKET_ACTION:assign:{index}"
                    ),
                ),
                InlineKeyboardButton(
                    text=t("admin_support_resolve", language),
                    callback_data=(
                        f"ADM_ADMIN_TICKET_ACTION:resolve:{index}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data=f"ADM_ADMIN_SUPPORT:{page}",
                )
            ],
        ]
    )

def format_admin_escalated_ticket(
    ticket,
    *,
    number: int,
    language: str,
) -> str:
    category = t(
        f"support_category_{ticket.category or 'other'}",
        language,
    )
    status = t(
        f"support_status_{ticket.status}",
        language,
    )
    user_number = f"user-{ticket.user_id.hex[:8]}"
    ticket_number = str(ticket.id).split("-", 1)[0]
    updated_at = (
        ticket.updated_at.strftime("%Y-%m-%d %H:%M")
        if ticket.updated_at
        else "-"
    )

    return t(
        "admin_escalated_ticket_card",
        language,
    ).format(
        number=number,
        ticket_number=ticket_number,
        user_number=user_number,
        category=category,
        priority=ticket.priority,
        status=status,
        updated_at=updated_at,
    )


def admin_escalated_ticket_item_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_open_btn", language),
                    callback_data=(
                        f"ADM_ADMIN_SUPPORT_OPEN:{index}"
                    ),
                )
            ]
        ]
    )


def admin_escalated_tickets_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []
    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"ADM_ADMIN_SUPPORT:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"ADM_ADMIN_SUPPORT:{page + 1}"
                ),
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

def format_support_staff_ticket_card(
    ticket,
    *,
    number: int,
    language: str,
) -> str:
    user_ref = f"user-{str(ticket.user_id)[:8]}"
    category = t(f"support_category_{ticket.category or 'other'}", language)
    status = t(f"support_status_{ticket.status}", language)
    age = ticket.created_at.strftime("%Y-%m-%d") if ticket.created_at else "-"

    return t("support_staff_ticket_card", language).format(
        number=number,
        ticket_id=str(ticket.id)[:8],
        user=user_ref,
        category=category,
        priority=t(f"support_priority_{(ticket.priority or 'P3').lower()}", language),
        age=age,
        status=status,
    )

def support_staff_ticket_card_keyboard(
    *,
    index: int,
    ticket,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("support_staff_open_ticket_btn", language).format(index=index + 1),
                callback_data=f"ADM_SUP_VIEW:{index}",
            )
        ]
    ]

    if ticket.status == "open":
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("support_staff_take_ticket_btn", language).format(index=index + 1),
                    callback_data=f"ADM_SUP_TAKE:{index}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_staff_ticket_actions_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_SUPPORT_VIEW:{view}:{page - 1}",
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_SUPPORT_VIEW:{view}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_staff_filter_btn", language),
                callback_data="ADM_SUPPORT_FILTERS",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_staff_back_to_panel", language),
                callback_data="ADM_SUPPORT",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def support_staff_filters_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("support_staff_open_btn", language),
                    callback_data="ADM_SUPPORT_VIEW:open:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_staff_in_progress_btn", language),
                    callback_data="ADM_SUPPORT_VIEW:in_progress:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_staff_resolved_btn", language),
                    callback_data="ADM_SUPPORT_VIEW:resolved:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("support_staff_back_to_panel", language),
                    callback_data="ADM_SUPPORT",
                )
            ],
        ]
    )

def format_support_staff_stats(stats: dict, language: str) -> str:
    counts = stats.get("counts") or {}
    avg_response_minutes = stats.get("avg_response_minutes")

    if avg_response_minutes is None:
        avg_response = t("support_staff_stats_no_response", language)
    else:
        avg_response = t("support_staff_stats_avg_minutes", language).format(
            minutes=avg_response_minutes,
        )

    return t("support_staff_stats_title", language).format(
        open_count=counts.get("open", 0),
        in_progress_count=counts.get("in_progress", 0),
        resolved_count=counts.get("resolved", 0),
        closed_count=counts.get("closed", 0),
        total_count=stats.get("total", 0),
        avg_response=avg_response,
    )


def support_staff_stats_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("support_staff_stats_period_btn", language),
                    callback_data="ADM_SUPPORT_STATS_PERIOD",
                ),
                InlineKeyboardButton(
                    text=t("support_staff_stats_category_btn", language),
                    callback_data="ADM_SUPPORT_STATS_CATEGORY",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("support_staff_back_to_panel", language),
                    callback_data="ADM_SUPPORT",
                )
            ],
        ]
    )

def support_ticket_keyboard(
    index: int,
    total: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_support_reply", language),
                callback_data=f"ADM_SUP_REPLY:{index}",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_support_assign", language),
                callback_data=f"ADM_SUP_ASSIGN:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_support_escalate", language),
                callback_data=f"ADM_SUP_ESCALATE:{index}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("admin_support_resolve", language),
                callback_data=f"ADM_SUP_RESOLVE:{index}",
            ),
            InlineKeyboardButton(
                text=t("admin_support_close", language),
                callback_data=f"ADM_SUP_CLOSE:{index}",
            ),
        ],
    ]

    nav = []
    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_SUP_VIEW:{index - 1}",
            )
        )
    if index < total - 1:
        nav.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_SUP_VIEW:{index + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("support_staff_back_to_queue", language),
                callback_data="ADM_SUPPORT",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_support_ticket_card(
    view,
    *,
    index: int,
    total: int,
    language: str,
) -> str:
    ticket = view.ticket
    messages = view.messages[-5:]

    status_text = t(f"support_status_{ticket.status}", language)
    category_text = t(f"support_category_{ticket.category or 'other'}", language)
    priority_text = t(f"support_priority_{(ticket.priority or 'P3').lower()}", language)
    user_ref = f"user-{str(ticket.user_id)[:8]}"

    created_at = ticket.created_at.strftime("%Y-%m-%d %H:%M") if ticket.created_at else "-"
    updated_at = ticket.updated_at.strftime("%Y-%m-%d %H:%M") if ticket.updated_at else "-"
    resolved_at = ticket.resolved_at.strftime("%Y-%m-%d %H:%M") if ticket.resolved_at else None

    lines = [
        t("admin_support_ticket_title", language).format(
            ticket_id=str(ticket.id)[:8],
            index=index + 1,
            total=total,
        ),
        "",
        f"{t('admin_support_category', language)}: {category_text}",
        f"{t('admin_support_user', language)}: {user_ref}",
        f"{t('admin_status', language)}: {status_text}",
        f"{t('admin_support_priority', language)}: {priority_text}",
        "",
        t("admin_support_history", language),
        t("admin_support_created_at", language).format(value=created_at),
        t("admin_support_updated_at", language).format(value=updated_at),
    ]

    if resolved_at:
        lines.append(
            t("admin_support_resolved_at", language).format(value=resolved_at)
        )

    lines.extend([
        "",
        t("admin_support_messages", language),
    ])

    if not messages:
        lines.append(t("admin_support_no_messages", language))
    else:
        for message in messages:
            sender_role = message.sender_role or "system"
            sender_label = t(f"support_sender_{sender_role}", language)

            text = (message.message_text or "").strip()
            if text == "[deleted by user request]":
                text = t("support_message_deleted_by_user", language)

            lines.append(
                t("support_message_line", language).format(
                    sender_role=sender_label,
                    message=text[:500],
                )
            )

    return "\n".join(lines)

def portfolio_reject_type_keyboard(
    *,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_portfolio_regular_reject_btn",
                        language,
                    ),
                    callback_data="ADM_PORT_REJECT_REGULAR",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_portfolio_forbidden_btn",
                        language,
                    ),
                    callback_data="ADM_PORT_REJECT_FORBIDDEN",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "moderator_changes_cancel_btn",
                        language,
                    ),
                    callback_data="ADM_PORT_REJECT_CANCEL",
                )
            ],
        ]
    )

def format_portfolio_moderation_card(
    view,
    *,
    index: int,
    page: int,
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

    mime_type = (
        view.storage_object.mime_type
        or "application/octet-stream"
    )
    size_kb = round(
        (view.storage_object.size_bytes or 0) / 1024,
        1,
    )

    owner_user_id = view.storage_object.owner_user_id
    owner = (
        f"user-{owner_user_id.hex[:8]}"
        if owner_user_id
        else "-"
    )

    caption = (
        (view.item.description or "").strip()
        or (view.item.title or "").strip()
        or t("moderator_portfolio_no_caption", language)
    )

    number = (
        page * MODERATOR_PORTFOLIO_PAGE_SIZE
        + index
        + 1
    )

    return t(
        "moderator_portfolio_card",
        language,
    ).format(
        page=page + 1,
        number=number,
        file_type=file_type,
        mime_type=mime_type,
        owner=owner,
        size_kb=size_kb,
        caption=caption[:500],
    )

def admin_specialist_card_keyboard(
    *,
    index: int,
    status: str,
    page: int,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    if status == "pending_moderation":
        rows.extend(
            [
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
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_request_changes_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_CHANGES:{index}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_blacklist_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_SP_SCOPED_BLOCK:{index}"
                        ),
                    )
                ],
            ]
        )
    else:
        if status == "approved":
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_hide_specialist_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_HIDE:{index}",
                    )
                ]
            )
        elif status == "hidden":
            rows.append(
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_restore_specialist_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_RESTORE:{index}",
                    )
                ]
            )

        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_specialist_read_only_btn",
                        language,
                    ),
                    callback_data="ADMIN_SPECIALIST_READ_ONLY",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data=(
                    f"ADM_ADMIN_SPECIALISTS:{status}:{page}"
                ),
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_admin_specialist_item(
    item,
    *,
    number: int,
    language: str,
) -> str:
    city = (
        item.city_name
        or t("admin_specialist_city_not_set", language)
    )
    created_at = (
        item.created_at.strftime("%Y-%m-%d")
        if item.created_at
        else "-"
    )

    return t("admin_specialist_item", language).format(
        number=number,
        name=item.display_name,
        profession=item.profession_name,
        city=city,
        status=item.status,
        date=created_at,
    )


def admin_specialist_item_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_open_btn", language),
                    callback_data=(
                        f"ADM_ADMIN_SPECIALIST_OPEN:{index}"
                    ),
                )
            ]
        ]
    )


def admin_specialists_keyboard(
    *,
    status: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_specialist_filter_btn", language),
                callback_data="ADM_ADMIN_SPECIALIST_FILTER",
            )
        ]
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"ADM_ADMIN_SPECIALISTS:{status}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"ADM_ADMIN_SPECIALISTS:{status}:{page + 1}"
                ),
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


def admin_specialist_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    statuses = (
        ("all", "admin_specialist_filter_all"),
        ("approved", "admin_specialist_filter_approved"),
        (
            "pending_moderation",
            "admin_specialist_filter_pending",
        ),
        ("draft", "admin_specialist_filter_draft"),
        ("hidden", "admin_specialist_filter_hidden"),
        ("rejected", "admin_specialist_filter_rejected"),
        ("blocked", "admin_specialist_filter_blocked"),
        ("deleted", "admin_specialist_filter_deleted"),
    )

    rows = [
        [
            InlineKeyboardButton(
                text=t(text_key, language),
                callback_data=(
                    f"ADM_ADMIN_SPECIALISTS:{status}:0"
                ),
            )
        ]
        for status, text_key in statuses
    ]

    rows.append(
        [
            InlineKeyboardButton(
                text=t("admin_panel_back", language),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_admin_user_history_item(
    card: AdminUserHistoryCard,
    *,
    number: int,
    language: str,
) -> str:
    return t(
        "admin_user_history_item",
        language,
    ).format(
        number=number,
        date=card.date,
        actor=card.actor,
        action=card.action,
        reason=card.reason,
        source=card.source,
    )


def admin_user_history_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_user_back_to_card_btn",
                        language,
                    ),
                    callback_data=f"ADM_USER_VIEW:{index}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_USERS",
                )
            ],
        ]
    )

def format_admin_user_roles(
    card: AdminUserDetailsCard,
    language: str,
) -> str:
    roles = (
        "\n".join(
            f"- {role}"
            for role in card.roles
        )
        if card.roles
        else t("admin_user_no_roles", language)
    )

    return t("admin_user_roles_text", language).format(
        user_number=card.user_number,
        roles=roles,
    )


def admin_user_roles_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_user_back_to_card_btn",
                        language,
                    ),
                    callback_data=f"ADM_USER_VIEW:{index}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_USERS",
                )
            ],
        ]
    )

def format_admin_user_details(
    card: AdminUserDetailsCard,
    language: str,
) -> str:
    roles = (
        ", ".join(card.roles)
        if card.roles
        else t("admin_user_no_roles", language)
    )

    blacklist = (
        t("admin_user_global_blocked", language)
        if card.is_global_blacklisted
        else t("admin_user_not_global_blocked", language)
    )

    return t("admin_user_details", language).format(
        user_number=card.user_number,
        display_name=card.display_name,
        username=card.username,
        roles=roles,
        status=card.status,
        last_seen=card.last_seen,
        complaints=card.complaints_count,
        blacklist=blacklist,
    )


def admin_user_details_keyboard(
    *,
    index: int,
    is_global_blacklisted: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_user_roles_btn", language),
                callback_data=f"ADM_USER_ROLES:{index}",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_user_history_btn", language),
                callback_data=f"ADM_USER_HISTORY:{index}",
            )
        ],
    ]

    if is_global_blacklisted:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_user_global_unblock_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_USER_GLOBAL_UNBLOCK:{index}"
                    ),
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_user_global_block_btn",
                        language,
                    ),
                    callback_data=(
                        f"ADM_USER_GLOBAL_BLOCK:{index}"
                    ),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_USERS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="ADM_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_admin_user_search_card(
    card: AdminUserSearchCard,
    *,
    number: int,
    language: str,
) -> str:
    return t("admin_user_search_card", language).format(
        number=number,
        user_number=card.user_number,
        telegram_id=card.telegram_id,
        username=card.username,
        display_name=card.display_name,
        status=card.status,
    )


def admin_user_search_result_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_open_btn", language),
                    callback_data=f"ADM_USER_VIEW:{index}",
                )
            ]
        ]
    )


def admin_user_search_actions_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_search_again_btn", language),
                    callback_data="ADM_USERS",
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

def format_admin_menu(
    summary: AdminMenuSummary,
    language: str,
) -> str:
    return t("admin_menu_text", language).format(
        users=summary.users,
        specialists=summary.specialists,
        tickets=summary.tickets,
        complaints=summary.complaints,
        blacklist=summary.blacklist,
        audit_alerts=summary.audit_alerts,
    )

def format_super_admin_menu(
    summary,
    language: str,
) -> str:
    return t("super_admin_menu_text", language).format(
        users=summary.users,
        specialists=summary.specialists,
        tickets=summary.tickets,
        complaints=summary.complaints,
        global_blacklist=summary.global_blacklist,
        system_alerts=summary.system_alerts,
        finance_alerts=summary.finance_alerts,
        audit_alerts=summary.audit_alerts,
    )

def minimal_admin_menu_keyboard(
    summary: AdminMenuSummary,
    language: str,
    *,
    show_role_switch: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_users_btn", language).format(
                    count=summary.users,
                ),
                callback_data="ADM_USERS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_specialists_btn", language).format(
                    count=summary.specialists,
                ),
                callback_data="ADM_ADMIN_SPECIALISTS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_support_btn", language).format(
                    count=summary.tickets,
                ),
                callback_data="ADM_ADMIN_SUPPORT",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_moderation_btn", language).format(
                    count=summary.complaints,
                ),
                callback_data="ADM_MODERATION_MENU",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_global_blacklist_btn", language).format(
                    count=summary.blacklist,
                ),
                callback_data="ADM_GLOBAL_BLACKLIST",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_audit_btn", language).format(
                    count=summary.audit_alerts,
                ),
                callback_data="ADM_LOGS",
            )
        ],
    ]

    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
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

def super_admin_menu_keyboard(
    summary,
    language: str,
    *,
    show_role_switch: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_users_roles_section_btn", language),
                callback_data="SA_USERS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dictionaries_section_btn", language),
                callback_data="ADM_DICT",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_moderation_section_btn", language),
                callback_data="ADM_MODERATION_MENU",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dialogs_section_btn", language),
                callback_data="ADM_DIALOGS_STUB",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_finance_section_btn", language),
                callback_data="SA_FINANCE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_promotion_section_btn", language),
                callback_data="ADM_PROMOTION_STUB",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_system_section_btn", language),
                callback_data="SA_SYSTEM",
            )
        ],
    ]

    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("search_menu", language),
                callback_data="MAIN_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_dictionaries_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_categories_btn", language),
                    callback_data="ADM_DICT_CATEGORIES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_professions_btn", language),
                    callback_data="ADM_DICT_PROFESSIONS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skills_btn", language),
                    callback_data="ADM_DICT_SKILLS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_languages_btn", language),
                    callback_data="ADM_DICT_LANGUAGES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_geo_btn", language),
                    callback_data="ADM_DICT_GEO",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_PANEL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

def super_admin_user_status_label(
    status: str | None,
    language: str,
) -> str:
    key_by_status = {
        "active": "super_admin_user_status_active",
        "blocked": "super_admin_user_status_blocked",
        "deleted": "super_admin_user_status_deleted",
    }

    normalized_status = (
        status or ""
    ).strip().lower()

    key = key_by_status.get(
        normalized_status
    )

    return t(
        key,
        language,
    ) if key else (
        status or "—"
    )


def super_admin_user_role_label(
    role: str | None,
    language: str,
) -> str:
    key_by_role = {
        "client": "super_admin_user_role_client",
        "specialist": "super_admin_user_role_specialist",
        "support": "super_admin_user_role_support",
        "moderator": "super_admin_user_role_moderator",
        "admin": "super_admin_user_role_admin",
        "super_admin": (
            "super_admin_user_role_super_admin"
        ),
        "finance_admin": (
            "super_admin_user_role_finance_admin"
        ),
        "content_manager": (
            "super_admin_user_role_content_manager"
        ),
    }

    normalized_role = (
        role or ""
    ).strip().lower()

    key = key_by_role.get(
        normalized_role
    )

    return t(
        key,
        language,
    ) if key else (
        role or "—"
    )


def super_admin_user_risk_label(
    risk_flags: str | None,
    language: str,
) -> str:
    normalized_value = (
        risk_flags or ""
    ).strip().lower()

    if normalized_value in {
        "",
        "-",
        "none",
    }:
        return t(
            "super_admin_user_risk_none",
            language,
        )

    if normalized_value.startswith("risk:"):
        return t(
            "super_admin_user_risk_score",
            language,
        ).format(
            score=normalized_value.split(
                ":",
                1,
            )[1]
        )

    return risk_flags or "—"

def format_super_admin_user_card(
    card,
    language: str,
) -> str:
    roles = (
        ", ".join(
            super_admin_user_role_label(
                role,
                language,
            )
            for role in card.roles
        )
        if card.roles
        else "—"
    )

    scopes = (
        ", ".join(card.scopes)
        if card.scopes
        else t(
            "super_admin_user_scopes_empty",
            language,
        )
    )

    return t("super_admin_user_card", language).format(
        name=card.display_name,
        user_number=card.user_number,
        telegram_id=card.telegram_id,
        username=card.username,
        status=super_admin_user_status_label(
            card.status,
            language,
        ),
        active_role=super_admin_user_role_label(
            card.active_role,
            language,
        ),
        roles=roles,
        scopes=scopes,
        last_seen=card.last_seen,
        risk_flags=super_admin_user_risk_label(
            card.risk_flags,
            language,
        ),
        complaints=card.complaints_count,
        blacklist=card.blacklist_count,
    )


def super_admin_user_card_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_user_roles_btn", language),
                    callback_data="SA_USER_ROLES",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_user_scopes_btn", language),
                    callback_data="SA_USER_SCOPES",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_user_audit_btn", language),
                    callback_data="SA_USER_AUDIT",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonate_btn", language),
                    callback_data="SA_USER_IMPERSONATE",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_global_blacklist_btn", language).format(
                        count=0,
                    ),
                    callback_data="SA_USER_GLOBAL_BLACKLIST",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                ),
            ],
        ]
    )

@admin_router.callback_query(F.data.startswith("SA_USER_OPEN:"))
async def super_admin_open_user_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        index = int(callback.data.split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    ids = data.get("super_admin_user_search_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_super_admin_user_details(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )

    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        super_admin_selected_user_id=str(
            target_user_id
        ),
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_user_card(
            card,
            language,
        ),
        reply_markup=super_admin_user_card_keyboard(
            language
        ),
    )

def format_super_admin_user_roles(
    items,
    language: str,
) -> str:
    if not items:
        return t("super_admin_user_roles_empty", language)

    lines = [
        t("super_admin_user_roles_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("super_admin_user_role_card", language).format(
                number=index,
                role=item.role,
                status=item.status,
                scope=item.scope,
                granted_by=item.granted_by,
                granted_at=item.granted_at,
            )
        )

    return "\n\n".join(lines)


def super_admin_user_roles_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_role_grant_btn", language),
                    callback_data="SA_ROLE_GRANT",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_role_revoke_btn", language),
                    callback_data="SA_ROLE_REVOKE",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_role_scope_btn", language),
                    callback_data="SA_ROLE_SCOPE",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_role_history_btn", language),
                    callback_data="SA_ROLE_HISTORY",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

def super_admin_scope_type_label(
    scope_type: str,
    language: str,
) -> str:
    return t(
        f"super_admin_scope_type_{scope_type}",
        language,
    )


def super_admin_scope_status_label(
    status: str,
    language: str,
) -> str:
    return t(
        f"super_admin_scope_status_{status}",
        language,
    )


def format_super_admin_role_scope_card(
    card: SuperAdminRoleScopeCard,
    *,
    number: int,
    language: str,
) -> str:
    lines = [
        t(
            "super_admin_scope_card_user",
            language,
        ).format(
            number=number,
            user_number=card.user_number,
        ),
        t(
            "super_admin_scope_card_role",
            language,
        ).format(
            role=super_admin_user_role_label(
                card.role,
                language,
            )
        ),
        t(
            "super_admin_scope_card_type",
            language,
        ).format(
            scope_type=super_admin_scope_type_label(
                card.scope_type,
                language,
            )
        ),
        t(
            "super_admin_scope_card_value",
            language,
        ).format(
            scope_value=card.scope_value,
        ),
        t(
            "super_admin_scope_card_status",
            language,
        ).format(
            status=super_admin_scope_status_label(
                card.status,
                language,
            )
        ),
        t(
            "super_admin_scope_card_reason",
            language,
        ).format(
            reason=card.reason or t(
                "super_admin_value_not_specified",
                language,
            )
        ),
        t(
            "super_admin_scope_card_granted_by",
            language,
        ).format(
            user_number=card.created_by or t(
                "super_admin_value_not_specified",
                language,
            )
        ),
        t(
            "super_admin_scope_card_created_at",
            language,
        ).format(
            created_at=card.created_at,
        ),
    ]

    if card.status == "revoked":
        lines.extend(
            [
                t(
                    "super_admin_scope_card_revoked_by",
                    language,
                ).format(
                    user_number=card.revoked_by or t(
                        "super_admin_value_not_specified",
                        language,
                    )
                ),
                t(
                    "super_admin_scope_card_revoked_at",
                    language,
                ).format(
                    revoked_at=card.revoked_at,
                ),
            ]
        )

    return "\n".join(lines)

def super_admin_role_scope_card_keyboard(
    *,
    index: int,
    status: str,
    language: str,
) -> InlineKeyboardMarkup | None:
    if status != "active":
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_scopes_revoke_btn", language),
                    callback_data=f"SA_SCOPE_REVOKE:{index}",
                )
            ]
        ]
    )

def super_admin_role_scopes_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    user_filtered: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("super_admin_scopes_view_active", language),
                callback_data=(
                    f"SA_SCOPES_QUEUE:active:{page}:"
                    f"{1 if user_filtered else 0}"
                ),
            ),
            InlineKeyboardButton(
                text=t("super_admin_scopes_view_history", language),
                callback_data=(
                    f"SA_SCOPES_QUEUE:history:{page}:"
                    f"{1 if user_filtered else 0}"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                text=t("super_admin_scopes_add_btn", language),
                callback_data=(
                    "SA_SCOPE_ADD_USER"
                    if user_filtered
                    else "SA_SCOPE_ADD"
                ),
            )
        ],
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_SCOPES_QUEUE:{view}:{page - 1}:"
                    f"{1 if user_filtered else 0}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_SCOPES_QUEUE:{view}:{page + 1}:"
                    f"{1 if user_filtered else 0}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append(
        [
            InlineKeyboardButton(
                text=t("super_admin_scopes_to_panel_btn", language),
                callback_data="SA_PANEL",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t("main_menu", language),
                callback_data="MAIN_MENU",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

@admin_router.callback_query(F.data == "SA_USER_ROLES")
async def super_admin_user_roles(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    target_user_id_raw = data.get("super_admin_selected_user_id")

    if not target_user_id_raw:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(target_user_id_raw)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await ModerationService(
                ModerationRepository(session)
            ).list_super_admin_user_roles(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )

    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_super_admin_user_roles(
            items,
            language,
        ),
        reply_markup=super_admin_user_roles_keyboard(
            language
        ),
    )

@admin_router.callback_query(F.data == "SA_SCOPES")
async def open_super_admin_role_scopes(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_super_admin_role_scopes_queue(
        callback,
        state,
        view="active",
        page=0,
        user_filtered=False,
    )


@admin_router.callback_query(F.data == "SA_USER_SCOPES")
async def open_super_admin_user_role_scopes(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_super_admin_role_scopes_queue(
        callback,
        state,
        view="active",
        page=0,
        user_filtered=True,
    )


@admin_router.callback_query(F.data.startswith("SA_SCOPES_QUEUE:"))
async def change_super_admin_role_scopes_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    view = (
        parts[1]
        if len(parts) > 1 and parts[1] in {"active", "history"}
        else "active"
    )

    try:
        page = max(0, int(parts[2]))
    except (IndexError, TypeError, ValueError):
        page = 0

    user_filtered = (
        len(parts) > 3
        and parts[3] == "1"
    )

    await open_super_admin_role_scopes_queue(
        callback,
        state,
        view=view,
        page=page,
        user_filtered=user_filtered,
    )

@admin_router.callback_query(
    F.data.in_({"SA_SCOPE_ADD", "SA_SCOPE_ADD_USER"})
)
async def ask_super_admin_scope_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    user_filtered = callback.data == "SA_SCOPE_ADD_USER"

    await state.set_state(
        AdminModerationFSM.entering_super_admin_scope_add
    )
    await state.update_data(
        super_admin_scope_add_user_filtered=user_filtered,
        super_admin_scope_add_payload=None,
    )

    if user_filtered:
        prompt = (
            "Введите role, scope_type, scope и причину через |.\n\n"
            "Формат:\n"
            "moderator | city | Lisbon | reason text\n\n"
            "Scope types: country, city, region, agency, community."
        )
    else:
        prompt = (
            "Введите user, role, scope_type, scope и причину через |.\n\n"
            "Формат:\n"
            "user-49ba690f | moderator | city | Lisbon | reason text\n\n"
            "Scope types: country, city, region, agency, community."
        )

    await callback.message.answer(
        prompt,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data="SA_SCOPE_ADD_CANCEL",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_super_admin_scope_add)
async def receive_super_admin_scope_add(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    data = await state.get_data()
    user_filtered = bool(
        data.get("super_admin_scope_add_user_filtered")
    )

    parts = [
        part.strip()
        for part in (message.text or "").split("|")
    ]

    expected_parts = 4 if user_filtered else 5

    if len(parts) != expected_parts:
        await message.answer(
            (
                "Неверный формат.\n"
                "Используйте разделитель | между полями."
            )
        )
        return

    if user_filtered:
        raw_selected_user_id = data.get("super_admin_selected_user_id")

        try:
            target_user_id = UUID(str(raw_selected_user_id))
        except (TypeError, ValueError):
            await state.set_state(None)
            await message.answer(
                t("super_admin_user_not_found", language)
            )
            return

        role, scope_type, scope_value, reason = parts
        target_user_label = f"user-{target_user_id.hex[:8]}"
    else:
        user_query, role, scope_type, scope_value, reason = parts

        admin_user_id, tenant_id, roles = (
            await get_admin_user_context(message.from_user.id)
        )

        if (
            not admin_user_id
            or not tenant_id
            or "super_admin" not in roles
        ):
            await state.set_state(None)
            await message.answer(
                t("admin_access_denied", language)
            )
            return

        try:
            async with get_session() as session:
                matches = await ModerationService(
                    ModerationRepository(session)
                ).search_super_admin_users(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    query=user_query,
                )
        except ModerationError as exc:
            await message.answer(str(exc))
            return

        if not matches:
            await message.answer(
                t("super_admin_user_not_found", language)
            )
            return

        if len(matches) > 1:
            await message.answer(
                (
                    "Найдено несколько пользователей. "
                    "Уточните user-facing ID, Telegram ID или username."
                )
            )
            return

        target_user_id = matches[0].user_id
        target_user_label = matches[0].user_number

    if len(reason.strip()) < 3:
        await message.answer(
            t("admin_reason_too_short", language)
        )
        return

    payload = {
        "user_id": str(target_user_id),
        "user_label": target_user_label,
        "role": role.strip().lower(),
        "scope_type": scope_type.strip().lower(),
        "scope_value": scope_value.strip(),
        "reason": reason.strip(),
    }

    await state.update_data(
        super_admin_scope_add_payload=payload,
    )
    await state.set_state(
        AdminModerationFSM.confirming_super_admin_scope_add
    )

    await message.answer(
        (
            "Подтвердите добавление territorial scope:\n\n"
            f"Пользователь: {payload['user_label']}\n"
            f"Роль: {payload['role']}\n"
            f"Scope type: {payload['scope_type']}\n"
            f"Scope: {payload['scope_value']}\n"
            f"Причина: {payload['reason']}"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подтвердить",
                        callback_data="SA_SCOPE_ADD_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_SCOPE_ADD_USER"
                            if user_filtered
                            else "SA_SCOPE_ADD"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data="SA_SCOPE_ADD_CANCEL",
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(
    AdminModerationFSM.confirming_super_admin_scope_add,
    F.data == "SA_SCOPE_ADD_CONFIRM",
)
async def execute_super_admin_scope_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    payload = data.get("super_admin_scope_add_payload") or {}

    try:
        target_user_id = UUID(str(payload.get("user_id")))
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            t("super_admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.set_state(None)
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).add_super_admin_role_scope(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                user_id=target_user_id,
                role=str(payload.get("role") or ""),
                scope_type=str(payload.get("scope_type") or ""),
                scope_value=str(payload.get("scope_value") or ""),
                reason=str(payload.get("reason") or ""),
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    user_filtered = bool(
        data.get("super_admin_scope_add_user_filtered")
    )

    await state.set_state(None)
    await state.update_data(
        super_admin_scope_add_payload=None,
    )

    await callback.message.answer(
        f"Scope добавлен. Статус: {result.status}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="К scopes",
                        callback_data=(
                            "SA_SCOPES_QUEUE:active:0:"
                            f"{1 if user_filtered else 0}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "SA_SCOPE_ADD_CANCEL")
async def cancel_super_admin_scope_add(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    user_filtered = bool(
        data.get("super_admin_scope_add_user_filtered")
    )

    await state.set_state(None)
    await state.update_data(
        super_admin_scope_add_payload=None,
    )

    await callback.message.answer(
        "Добавление scope отменено.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="К scopes",
                        callback_data=(
                            "SA_SCOPES_QUEUE:active:0:"
                            f"{1 if user_filtered else 0}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("SA_SCOPE_REVOKE:"))
async def ask_super_admin_scope_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("super_admin_scope_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    scope_ids = data.get("super_admin_scope_ids") or []

    if index < 0 or index >= len(scope_ids):
        await callback.answer(
            t("super_admin_scope_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_super_admin_scope_revoke
    )
    await state.update_data(
        super_admin_scope_revoke_index=index,
        super_admin_scope_revoke_reason=None,
    )

    await callback.message.answer(
        t("super_admin_scope_revoke_reason_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data="SA_SCOPE_REVOKE_CANCEL",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_super_admin_scope_revoke)
async def receive_super_admin_scope_revoke_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(
            t("admin_reason_too_short", language)
        )
        return

    data = await state.get_data()
    index = data.get("super_admin_scope_revoke_index")
    scope_ids = data.get("super_admin_scope_ids") or []
    scope_labels = data.get(
        "super_admin_scope_labels"
    ) or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(scope_ids)
        or index >= len(scope_labels)
    ):
        await state.set_state(None)
        await message.answer(
            t("super_admin_scope_not_found", language)
        )
        return

    await state.update_data(
        super_admin_scope_revoke_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_super_admin_scope_revoke
    )

    await message.answer(
        t(
            "super_admin_scope_revoke_confirm",
            language,
        ).format(
            scope_label=scope_labels[index],
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_scope_revoke_confirm_btn",
                            language,
                        ),
                        callback_data="SA_SCOPE_REVOKE_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=f"SA_SCOPE_REVOKE:{index}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data="SA_SCOPE_REVOKE_CANCEL",
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(
    AdminModerationFSM.confirming_super_admin_scope_revoke,
    F.data == "SA_SCOPE_REVOKE_CONFIRM",
)
async def execute_super_admin_scope_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("super_admin_scope_revoke_index")
    reason = data.get("super_admin_scope_revoke_reason")
    scope_ids = data.get("super_admin_scope_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(scope_ids)
    ):
        await state.set_state(None)
        await callback.answer(
            "Scope не найден.",
            show_alert=True,
        )
        return

    try:
        scope_id = UUID(str(scope_ids[index]))
    except (TypeError, ValueError):
        await state.set_state(None)
        await callback.answer(
            "Scope не найден.",
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.set_state(None)
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).revoke_super_admin_role_scope(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                scope_id=scope_id,
                reason=reason,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    user_filtered = bool(
        data.get("super_admin_scope_user_filtered")
    )

    await state.set_state(None)
    await state.update_data(
        super_admin_scope_revoke_index=None,
        super_admin_scope_revoke_reason=None,
    )

    await callback.message.answer(
        t("super_admin_scope_revoke_success", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("super_admin_scopes_to_list_btn", language),
                        callback_data=(
                            "SA_SCOPES_QUEUE:active:0:"
                            f"{1 if user_filtered else 0}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("super_admin_scopes_view_history", language),
                        callback_data=(
                            "SA_SCOPES_QUEUE:history:0:"
                            f"{1 if user_filtered else 0}"
                        ),
                    )
                ],
            ]
        ),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "SA_SCOPE_REVOKE_CANCEL")
async def cancel_super_admin_scope_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    user_filtered = bool(
        data.get("super_admin_scope_user_filtered")
    )

    await state.set_state(None)
    await state.update_data(
        super_admin_scope_revoke_index=None,
        super_admin_scope_revoke_reason=None,
    )

    await callback.message.answer(
        t("super_admin_scope_revoke_cancelled", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("super_admin_scopes_to_list_btn", language),
                        callback_data=(
                            "SA_SCOPES_QUEUE:active:0:"
                            f"{1 if user_filtered else 0}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

async def open_super_admin_role_scopes_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str,
    page: int,
    user_filtered: bool,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    selected_user_id = None

    if user_filtered:
        raw_selected_user_id = data.get("super_admin_selected_user_id")

        try:
            selected_user_id = UUID(str(raw_selected_user_id))
        except (TypeError, ValueError):
            await callback.answer(
                t("super_admin_user_not_found", language),
                show_alert=True,
            )
            return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).open_super_admin_role_scopes(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                user_id=selected_user_id,
                view=view,
                page=page,
                page_size=5,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_scope_ids=[
            str(card.scope_id)
            for card in result.items
        ],
        super_admin_scope_labels=[
            (
                f"{super_admin_scope_type_label(
                    card.scope_type,
                    language,
                )}: {card.scope_value}"
            )
            for card in result.items
        ],
        super_admin_scope_user_ids=[
            str(card.user_id)
            for card in result.items
        ],
        super_admin_scope_view=result.view,
        super_admin_scope_page=result.page,
        super_admin_scope_user_filtered=user_filtered,
    )

    view_label = t(
        (
            "super_admin_scopes_view_history"
            if result.view == "history"
            else "super_admin_scopes_view_active"
        ),
        language,
    )

    title_lines = [
        t("super_admin_scopes_title", language),
        t("super_admin_scopes_section", language).format(
            view=view_label,
        ),
        t("super_admin_scopes_count", language).format(
            count=len(result.items),
        ),
    ]

    if selected_user_id:
        title_lines.append(
            t("super_admin_scopes_for_user", language).format(
                user_number=f"user-{selected_user_id.hex[:8]}",
            )
        )

    await callback.answer()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            data.get("last_menu_message_id"),
            *(
                data.get(
                    "admin_scope_list_message_ids"
                )
                or []
            ),
        ],
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        "\n".join(title_lines)
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not result.items:
        empty_message = await callback.message.answer(
            t(
                "super_admin_scopes_empty",
                language,
            ),
            reply_markup=(
                super_admin_role_scopes_keyboard(
                    view=result.view,
                    page=result.page,
                    has_next=False,
                    user_filtered=user_filtered,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            admin_scope_list_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        return

    start_number = result.page * 5 + 1

    for offset, card in enumerate(result.items):
        card_message = await callback.message.answer(
            format_super_admin_role_scope_card(
                card,
                number=start_number + offset,
                language=language,
            ),
            reply_markup=(
                super_admin_role_scope_card_keyboard(
                    index=offset,
                    status=card.status,
                    language=language,
                )
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "super_admin_scopes_actions",
            language,
        ),
        reply_markup=super_admin_role_scopes_keyboard(
            view=result.view,
            page=result.page,
            has_next=result.has_next,
            user_filtered=user_filtered,
            language=language,
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        admin_scope_list_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )


def format_super_admin_permissions(
    items,
    language: str,
) -> str:
    if not items:
        return t("super_admin_permissions_empty", language)

    lines = [
        t("super_admin_permissions_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("super_admin_permission_card", language).format(
                number=index,
                role=item.role,
                permission_code=item.permission_code,
                scope=item.scope,
                status=item.status,
                granted_by=item.granted_by,
                created_at=item.created_at,
                description=item.description,
            )
        )

    return "\n\n".join(lines)


def super_admin_permissions_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_search_btn", language),
                    callback_data="SA_PERMISSION_SEARCH",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_grant_btn", language),
                    callback_data="SA_PERMISSION_GRANT",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_permission_revoke_btn", language),
                    callback_data="SA_PERMISSION_REVOKE",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_history_btn", language),
                    callback_data="SA_PERMISSION_HISTORY",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_back_to_menu_btn", language),
                    callback_data="ADM_PANEL",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

def super_admin_permission_confirm_keyboard(
    action: str,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_confirm_btn", language),
                    callback_data=f"SA_PERMISSION_{action.upper()}_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_permission_cancel_btn", language),
                    callback_data="SA_PERMISSION_CANCEL",
                )
            ],
        ]
    )

async def show_super_admin_permissions(
    event: CallbackQuery | Message,
    state: FSMContext,
    *,
    query: str = "",
    actor_telegram_id: int | str | None = None,
) -> None:
    language = normalize_language(
        event.from_user.language_code
    )
    callback_answered = False

    if isinstance(event, CallbackQuery):
        await event.answer()
        callback_answered = True

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            actor_telegram_id
            or event.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        error_text = t(
            "admin_access_denied",
            language,
        )

        if isinstance(event, CallbackQuery):
            await replace_admin_callback_screen(
                callback=event,
                state=state,
                text=error_text,
                callback_answered=callback_answered,
            )
        else:
            await replace_admin_input_screen(
                message=event,
                state=state,
                text=error_text,
            )
        return

    try:
        async with get_session() as session:
            items = await ModerationService(
                ModerationRepository(session)
            ).list_super_admin_permission_matrix(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                query=query,
                limit=10,
            )

    except ModerationError as exc:
        error_text = str(exc)

        if isinstance(event, CallbackQuery):
            await replace_admin_callback_screen(
                callback=event,
                state=state,
                text=error_text,
                callback_answered=callback_answered,
            )
        else:
            await replace_admin_input_screen(
                message=event,
                state=state,
                text=error_text,
            )
        return

    await state.update_data(
        super_admin_permission_query=query,
    )
    await state.set_state(None)

    text = format_super_admin_permissions(
        items,
        language,
    )
    keyboard = super_admin_permissions_keyboard(
        language
    )

    if isinstance(event, CallbackQuery):
        await replace_admin_callback_screen(
            callback=event,
            state=state,
            text=text,
            reply_markup=keyboard,
            callback_answered=callback_answered,
        )
    else:
        await replace_admin_input_screen(
            message=event,
            state=state,
            text=text,
            reply_markup=keyboard,
        )

@admin_router.callback_query(F.data == "SA_PERMISSIONS")
async def super_admin_permissions(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_super_admin_permissions(
        callback,
        state,
        query="",
    )


@admin_router.callback_query(F.data == "SA_PERMISSION_SEARCH")
async def super_admin_permission_search_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.set_state(
        AdminModerationFSM.entering_super_admin_permission_search
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_search_prompt",
            language,
        ),
    )


@admin_router.message(AdminModerationFSM.entering_super_admin_permission_search)
async def super_admin_permission_search_message(
    message: Message,
    state: FSMContext,
):
    query = (message.text or "").strip()

    await show_super_admin_permissions(
        message,
        state,
        query=query,
    )

@admin_router.callback_query(F.data == "SA_PERMISSION_GRANT")
async def super_admin_permission_grant_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.set_state(
        AdminModerationFSM.entering_super_admin_permission_grant
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_action_format",
            language,
        ),
    )

@admin_router.callback_query(F.data == "SA_PERMISSION_REVOKE")
async def super_admin_permission_revoke_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.set_state(
        AdminModerationFSM.entering_super_admin_permission_revoke
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_action_format",
            language,
        ),
    )

@admin_router.callback_query(F.data == "SA_PERMISSION_HISTORY")
async def super_admin_permission_history(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await open_super_admin_audit_queue(
        callback,
        state,
        target_type="permission",
        page=0,
    )

@admin_router.message(
    AdminModerationFSM.entering_super_admin_permission_grant
)
async def super_admin_permission_grant_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    parsed = parse_super_admin_permission_action(
        message.text
    )

    if not parsed:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "super_admin_permission_bad_format",
                language,
            ),
        )
        return

    role, permission_code, reason = parsed

    await state.update_data(
        super_admin_permission_action="grant",
        super_admin_permission_role=role,
        super_admin_permission_code=permission_code,
        super_admin_permission_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_super_admin_permission_grant
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "super_admin_permission_grant_confirm",
            language,
        ).format(
            role=role,
            permission_code=permission_code,
            reason=reason,
        ),
        reply_markup=(
            super_admin_permission_confirm_keyboard(
                "grant",
                language,
            )
        ),
    )

@admin_router.message(
    AdminModerationFSM.entering_super_admin_permission_revoke
)
async def super_admin_permission_revoke_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    parsed = parse_super_admin_permission_action(
        message.text
    )

    if not parsed:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "super_admin_permission_bad_format",
                language,
            ),
        )
        return

    role, permission_code, reason = parsed

    await state.update_data(
        super_admin_permission_action="revoke",
        super_admin_permission_role=role,
        super_admin_permission_code=permission_code,
        super_admin_permission_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_super_admin_permission_revoke
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "super_admin_permission_revoke_confirm",
            language,
        ).format(
            role=role,
            permission_code=permission_code,
            reason=reason,
        ),
        reply_markup=(
            super_admin_permission_confirm_keyboard(
                "revoke",
                language,
            )
        ),
    )

@admin_router.callback_query(F.data == "SA_PERMISSION_CANCEL")
async def super_admin_permission_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.update_data(
        super_admin_permission_action=None,
        super_admin_permission_role=None,
        super_admin_permission_code=None,
        super_admin_permission_reason=None,
    )
    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_cancelled",
            language,
        ),
        reply_markup=super_admin_permissions_keyboard(
            language
        ),
    )


@admin_router.callback_query(F.data == "SA_PERMISSION_GRANT_CONFIRM")
async def super_admin_permission_grant_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    await super_admin_permission_execute(callback, state, expected_action="grant")


@admin_router.callback_query(F.data == "SA_PERMISSION_REVOKE_CONFIRM")
async def super_admin_permission_revoke_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    await super_admin_permission_execute(callback, state, expected_action="revoke")


async def super_admin_permission_execute(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    expected_action: str,
):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    action = data.get("super_admin_permission_action")
    role = data.get("super_admin_permission_role")
    permission_code = data.get("super_admin_permission_code")
    reason = data.get("super_admin_permission_reason")

    if action != expected_action or not role or not permission_code or not reason:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            service = ModerationService(ModerationRepository(session))

            if action == "grant":
                await service.grant_super_admin_permission(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    role=role,
                    permission_code=permission_code,
                    reason=reason,
                )
            else:
                await service.revoke_super_admin_permission(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    role=role,
                    permission_code=permission_code,
                    reason=reason,
                )

    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        super_admin_permission_action=None,
        super_admin_permission_role=None,
        super_admin_permission_code=None,
        super_admin_permission_reason=None,
    )
    await state.set_state(None)

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_permission_changed",
            language,
        ),
        reply_markup=super_admin_permissions_keyboard(
            language
        ),
    )

def super_admin_impersonation_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_client_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:client",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_specialist_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:specialist",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_support_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:support",
                ),
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_moderator_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:moderator",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_admin_btn", language),
                    callback_data="SA_IMPERSONATE_ROLE:admin",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("super_admin_impersonation_stop_btn", language),
                    callback_data="SA_IMPERSONATE_STOP",
                ),
            ],
        ]
    )

def super_admin_impersonation_read_only_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

def super_admin_read_only_client_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_client_dialogs_btn",
                        language,
                    ),
                    callback_data="SA_RO_CLIENT_DIALOGS:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

def super_admin_read_only_specialist_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_specialist_profile_btn",
                        language,
                    ),
                    callback_data="SA_RO_SPECIALIST_PROFILE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_specialist_dialogs_btn",
                        language,
                    ),
                    callback_data="SA_RO_SPECIALIST_DIALOGS:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )


def super_admin_read_only_specialist_dialogs_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_SPECIALIST_DIALOGS:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_SPECIALIST_DIALOGS:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_RO_SPECIALIST_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_super_admin_read_only_message_history(
    messages,
    *,
    other_name: str,
    language: str,
) -> str:
    history_lines = []

    for message in messages:
        sender_name = (
            t("contact_chat_you_label", language)
            if message.is_sent_by_viewer
            else other_name
        )
        sent_at = message.created_at.strftime(
            "%d.%m %H:%M"
        )
        message_body = (
            format_chat_message_body(
                message,
                language,
            )
            or "—"
        )

        history_lines.append(
            f"{sender_name} · {sent_at}\n"
            f"{message_body}"
        )

    return "\n\n".join(history_lines) or "—"



def format_super_admin_read_only_specialist_dialog(
    item,
    *,
    number: int,
    language: str,
) -> str:
    message = (item.last_message_text or "").strip()

    if len(message) > 300:
        message = f"{message[:297]}..."

    return t(
        "super_admin_ro_specialist_dialog_item",
        language,
    ).format(
        number=number,
        client=item.specialist_name or "-",
        profession=item.profession_name or "-",
        status=admin_dialog_status_label(
            item.status,
            language,
        ),
        unread=item.unread_count,
        message=message or "-",
    )


def format_super_admin_read_only_specialist_dialog_detail(
    detail,
    *,
    language: str,
) -> str:
    messages = format_super_admin_read_only_message_history(
        detail.messages,
        other_name=detail.client_name or "—",
        language=language,
    )

    return t(
        "super_admin_ro_specialist_dialog_detail",
        language,
    ).format(
        client=detail.client_name or "—",
        profession=detail.profession_name or "—",
        status=admin_dialog_status_label(
            detail.thread_status,
            language,
        ),
        messages=messages,
    )

def super_admin_read_only_client_dialogs_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_CLIENT_DIALOGS:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_CLIENT_DIALOGS:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_RO_CLIENT_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_super_admin_read_only_client_dialog(
    item,
    *,
    number: int,
    language: str,
) -> str:
    message = (item.last_message_text or "").strip()

    if len(message) > 300:
        message = f"{message[:297]}..."

    return t(
        "super_admin_ro_client_dialog_item",
        language,
    ).format(
        number=number,
        specialist=item.specialist_name or "-",
        profession=item.profession_name or "-",
        status=admin_dialog_status_label(
            item.status,
            language,
        ),
        unread=item.unread_count,
        message=message or "-",
    )


def format_super_admin_read_only_client_dialog_detail(
    detail,
    *,
    language: str,
) -> str:
    messages = format_super_admin_read_only_message_history(
        detail.messages,
        other_name=detail.specialist_name or "—",
        language=language,
    )

    return t(
        "super_admin_ro_client_dialog_detail",
        language,
    ).format(
        specialist=detail.specialist_name or "—",
        profession=detail.profession_name or "—",
        status=admin_dialog_status_label(
            detail.thread_status,
            language,
        ),
        messages=messages,
    )

def super_admin_read_only_support_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("support_staff_open_btn", language),
                    callback_data="SA_RO_SUPPORT_LIST:open:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "support_staff_in_progress_btn",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_SUPPORT_LIST:in_progress:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "support_staff_resolved_btn",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_SUPPORT_LIST:resolved:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )


def super_admin_read_only_support_list_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("support_staff_open_btn", language),
                callback_data="SA_RO_SUPPORT_LIST:open:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "support_staff_in_progress_btn",
                    language,
                ),
                callback_data=(
                    "SA_RO_SUPPORT_LIST:in_progress:0"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "support_staff_resolved_btn",
                    language,
                ),
                callback_data=(
                    "SA_RO_SUPPORT_LIST:resolved:0"
                ),
            )
        ],
    ]

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_SUPPORT_LIST:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_SUPPORT_LIST:{view}:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_support_back_btn",
                        language,
                    ),
                    callback_data="SA_RO_SUPPORT_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_moderator_menu_keyboard(
    language: str,
    *,
    back_callback: str = "SA_IMPERSONATE_MENU",
    back_text_key: str = (
        "super_admin_impersonation_change_cabinet_btn"
    ),
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_pending_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_QUEUE:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_portfolio_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_PORTFOLIO",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_reviews_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_REVIEWS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_blacklist_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_BLACKLIST:active:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_complaints_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_COMPLAINTS:open:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(back_text_key, language),
                    callback_data=back_callback,
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )


def super_admin_read_only_moderator_queue_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_RO_MOD_QUEUE:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_MOD_QUEUE:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_moderator_complaints_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_open",
                    language,
                ),
                callback_data="SA_RO_MOD_COMPLAINTS:open:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_new",
                    language,
                ),
                callback_data="SA_RO_MOD_COMPLAINTS:new:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_review",
                    language,
                ),
                callback_data=(
                    "SA_RO_MOD_COMPLAINTS:in_review:0"
                ),
            ),
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_resolved",
                    language,
                ),
                callback_data=(
                    "SA_RO_MOD_COMPLAINTS:resolved:0"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_complaint_filter_rejected",
                    language,
                ),
                callback_data=(
                    "SA_RO_MOD_COMPLAINTS:rejected:0"
                ),
            )
        ],
    ]

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_MOD_COMPLAINTS:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_MOD_COMPLAINTS:{view}:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_moderator_portfolio_keyboard(
    *,
    index: int,
    total: int,
    page: int,
    has_next_page: bool,
    signed_url: str,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t("portfolio_open_button", language),
                url=signed_url,
            )
        ]
    ]

    item_navigation: list[InlineKeyboardButton] = []

    if index > 0:
        item_navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_RO_MOD_PORT_VIEW:{index - 1}",
            )
        )

    if index + 1 < total:
        item_navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_MOD_PORT_VIEW:{index + 1}",
            )
        )

    if item_navigation:
        rows.append(item_navigation)

    page_navigation: list[InlineKeyboardButton] = []

    if page > 0 and index == 0:
        page_navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_RO_MOD_PORT_QUEUE:{page - 1}",
            )
        )

    if has_next_page and index == total - 1:
        page_navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_MOD_PORT_QUEUE:{page + 1}",
            )
        )

    if page_navigation:
        rows.append(page_navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_to_portfolio_btn",
                        language,
                    ),
                    callback_data=f"SA_RO_MOD_PORT_QUEUE:{page}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_moderator_reviews_keyboard(
    *,
    index: int,
    total: int,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if index > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_RO_MOD_REV_VIEW:{index - 1}",
            )
        )
    elif page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_MOD_REVIEWS_PAGE:{page - 1}"
                ),
            )
        )

    if index + 1 < total:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_MOD_REV_VIEW:{index + 1}",
            )
        )
    elif has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_MOD_REVIEWS_PAGE:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_to_reviews_btn",
                        language,
                    ),
                    callback_data=(
                        f"SA_RO_MOD_REVIEWS_PAGE:{page}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_moderator_blacklist_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t(
                    "moderator_blacklist_active_btn",
                    language,
                ),
                callback_data="SA_RO_MOD_BLACKLIST:active:0",
            ),
            InlineKeyboardButton(
                text=t(
                    "moderator_blacklist_history_btn",
                    language,
                ),
                callback_data="SA_RO_MOD_BLACKLIST:revoked:0",
            ),
        ]
    ]

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_MOD_BLACKLIST:{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_MOD_BLACKLIST:{view}:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_btn",
                        language,
                    ),
                    callback_data="SA_RO_MOD_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_admin_menu_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_users_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_USERS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_moderation_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_MODERATION",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_specialists_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_SPECIALISTS:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_escalated_tickets_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_SUPPORT:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_global_blacklist_btn",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_GLOBAL_BLACKLIST:active:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_audit_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_AUDIT_QUEUE:all:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_MENU",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )


def super_admin_read_only_admin_audit_filter_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_audit_filter_all", language),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:all:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_users",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:user:0"
                    ),
                ),
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_specialists",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:specialist:0"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_support",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:"
                        "support_ticket:0"
                    ),
                ),
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_complaints",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:complaint:0"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_reviews",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:review:0"
                    ),
                ),
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_portfolio",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:"
                        "specialist_portfolio_item:0"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_audit_filter_blacklist",
                        language,
                    ),
                    callback_data=(
                        "SA_RO_ADMIN_AUDIT_QUEUE:blacklist:0"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_moderator_back_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

def super_admin_read_only_admin_global_blacklist_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_active_title",
                    language,
                ),
                callback_data=(
                    "SA_RO_ADMIN_GLOBAL_BLACKLIST:active:0"
                ),
            ),
            InlineKeyboardButton(
                text=t(
                    "admin_global_blacklist_history_title",
                    language,
                ),
                callback_data=(
                    "SA_RO_ADMIN_GLOBAL_BLACKLIST:history:0"
                ),
            ),
        ]
    ]

    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    "SA_RO_ADMIN_GLOBAL_BLACKLIST:"
                    f"{view}:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    "SA_RO_ADMIN_GLOBAL_BLACKLIST:"
                    f"{view}:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_admin_support_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"SA_RO_ADMIN_SUPPORT:{page - 1}",
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"SA_RO_ADMIN_SUPPORT:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_admin_specialists_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=(
                    f"SA_RO_ADMIN_SPECIALISTS:{page - 1}"
                ),
            )
        )

    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=(
                    f"SA_RO_ADMIN_SPECIALISTS:{page + 1}"
                ),
            )
        )

    if navigation:
        rows.append(navigation)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def super_admin_read_only_admin_user_search_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_change_cabinet_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_HOME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )


def super_admin_read_only_admin_user_details_keyboard(
    *,
    index: int,
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_user_roles_btn", language),
                    callback_data=f"SA_RO_ADMIN_USER_ROLES:{index}",
                ),
                InlineKeyboardButton(
                    text=t("admin_user_history_btn", language),
                    callback_data=(
                        f"SA_RO_ADMIN_USER_HISTORY:{index}"
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_ro_admin_back_to_users_btn",
                        language,
                    ),
                    callback_data="SA_RO_ADMIN_USERS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "super_admin_impersonation_stop_btn",
                        language,
                    ),
                    callback_data="SA_IMPERSONATE_STOP",
                )
            ],
        ]
    )

def format_super_admin_read_only_support_ticket(
    ticket,
    *,
    number: int,
    language: str,
) -> str:
    updated_at = (
        ticket.updated_at.strftime("%Y-%m-%d %H:%M")
        if ticket.updated_at
        else "-"
    )

    return t(
        "super_admin_ro_support_ticket_item",
        language,
    ).format(
        number=number,
        category=t(
            f"support_category_{ticket.category or 'other'}",
            language,
        ),
        status=t(
            f"support_status_{ticket.status}",
            language,
        ),
        priority=t(
            f"support_priority_{(ticket.priority or 'P3').lower()}",
            language,
        ),
        updated_at=updated_at,
    )


def format_super_admin_read_only_support_ticket_detail(
    ticket_view,
    *,
    number: int,
    language: str,
) -> str:
    ticket = ticket_view.ticket
    messages = ticket_view.messages[-10:]

    lines = [
        t(
            "super_admin_ro_support_ticket_title",
            language,
        ).format(number=number),
        "",
        f"{t('admin_support_category', language)}: "
        f"{t(f'support_category_{ticket.category or 'other'}', language)}",
        f"{t('admin_status', language)}: "
        f"{t(f'support_status_{ticket.status}', language)}",
        f"{t('admin_support_priority', language)}: "
        f"{t(f'support_priority_{(ticket.priority or 'P3').lower()}', language)}",
        "",
        t("admin_support_messages", language),
    ]

    if not messages:
        lines.append(
            t("admin_support_no_messages", language)
        )

    for message in messages:
        sender_role = message.sender_role or "system"
        message_text = (
            (message.message_text or "").strip()
            or t("super_admin_value_not_specified", language)
        )

        lines.append(
            t("support_message_line", language).format(
                sender_role=t(
                    f"support_sender_{sender_role}",
                    language,
                ),
                message=message_text[:500],
            )
        )

    lines.extend(
        [
            "",
            t("super_admin_ro_read_only_label", language),
        ]
    )

    return "\n".join(lines)

def super_admin_preview_status_label(
    status: str,
    language: str,
) -> str:
    return t(
        f"super_admin_preview_status_{status}",
        language,
    )


def super_admin_preview_availability_label(
    is_available: bool,
    language: str,
) -> str:
    key = (
        "super_admin_preview_availability_available"
        if is_available
        else "super_admin_preview_availability_unavailable"
    )

    return t(key, language)

async def show_super_admin_specialist_read_only_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()
    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )
    target_role = data.get(
        "super_admin_impersonation_target_role"
    )
    is_read_only = bool(
        data.get("super_admin_impersonation_read_only")
    )

    if (
        not target_user_id_raw
        or target_role != "specialist"
        or not is_read_only
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            cabinet = await ModerationService(
                ModerationRepository(session)
            ).get_specialist_read_only_cabinet(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                language=language,
            )

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return
    await state.update_data(
        super_admin_impersonation_specialist_id=str(
            cabinet.specialist_id
        ),
    )
    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_specialist_cabinet",
            language,
        ).format(
            user_number=cabinet.user_number,
            display_name=cabinet.display_name,
            professions=(
                ", ".join(cabinet.professions)
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            status=super_admin_preview_status_label(
                cabinet.status,
                language,
            ),
            availability=(
                super_admin_preview_availability_label(
                    cabinet.is_available,
                    language,
                )
            ),
            dialogs_unread=cabinet.dialogs_unread,
        ),
        reply_markup=(
            super_admin_read_only_specialist_menu_keyboard(
                language
            )
        ),
    )

async def show_super_admin_support_read_only_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()
    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )
    target_role = data.get(
        "super_admin_impersonation_target_role"
    )
    is_read_only = bool(
        data.get("super_admin_impersonation_read_only")
    )

    if (
        not target_user_id_raw
        or target_role != "support"
        or not is_read_only
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            cabinet = await ModerationService(
                ModerationRepository(session)
            ).get_support_read_only_cabinet(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            (
                "super_admin_ro_admin_moderation_cabinet"
                if target_role == "admin"
                else (
                    "super_admin_impersonation_moderator_cabinet"
                )
            ),
            language,
        ).format(
            user_number=(
                data.get(
                    "super_admin_impersonation_target_user_number"
                )
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            )
        ),
        reply_markup=(
            super_admin_read_only_moderator_menu_keyboard(
                language,
                back_callback=(
                    "SA_RO_ADMIN_HOME"
                    if target_role == "admin"
                    else "SA_IMPERSONATE_MENU"
                ),
                back_text_key=(
                    "super_admin_ro_admin_back_to_dashboard_btn"
                    if target_role == "admin"
                    else (
                        "super_admin_impersonation_change_cabinet_btn"
                    )
                ),
            )
        ),
    )

async def show_super_admin_moderator_read_only_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    target_role = str(
        data.get(
            "super_admin_impersonation_target_role"
        )
        or ""
    )

    if (
        not data.get(
            "super_admin_impersonation_read_only"
        )
        or data.get(
            "super_admin_impersonation_target_role"
        )
        not in READ_ONLY_MODERATION_TARGET_ROLES
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )

    try:
        UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            (
                "super_admin_ro_admin_moderation_cabinet"
                if target_role == "admin"
                else (
                    "super_admin_impersonation_moderator_cabinet"
                )
            ),
            language,
        ).format(
            user_number=(
                data.get(
                    "super_admin_impersonation_target_user_number"
                )
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            )
        ),
        reply_markup=(
            super_admin_read_only_moderator_menu_keyboard(
                language,
                back_callback=(
                    "SA_RO_ADMIN_HOME"
                    if target_role == "admin"
                    else "SA_IMPERSONATE_MENU"
                ),
                back_text_key=(
                    "super_admin_ro_admin_back_to_dashboard_btn"
                    if target_role == "admin"
                    else (
                        "super_admin_impersonation_change_cabinet_btn"
                    )
                ),
            )
        ),
    )

@admin_router.callback_query(F.data == "SA_RO_MOD_HOME")
async def super_admin_read_only_moderator_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_super_admin_moderator_read_only_cabinet(
        callback,
        state,
    )


@admin_router.callback_query(
    F.data.startswith("SA_RO_MOD_QUEUE:")
)
async def super_admin_read_only_moderator_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int((callback.data or "").split(":", 1)[1]),
            0,
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await ModerationService(
                ModerationRepository(session)
            ).open_pending_specialists_queue(
                moderator_user_id=target_user_id,
                tenant_id=tenant_id,
                page=page,
                page_size=MODERATOR_PROFILE_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = items[:MODERATOR_PROFILE_PAGE_SIZE]
    has_next = len(items) > MODERATOR_PROFILE_PAGE_SIZE

    await state.update_data(
        super_admin_impersonation_moderator_profile_ids=[
            str(item.specialist_id)
            for item in visible_items
        ],
        super_admin_impersonation_moderator_page=page,
    )

    await callback.message.answer(
        t(
            "super_admin_ro_moderator_queue_title",
            language,
        ).format(
            page=page + 1,
            count=len(visible_items),
        )
    )

    start_number = page * MODERATOR_PROFILE_PAGE_SIZE + 1

    for index, item in enumerate(visible_items):
        number = start_number + index

        await callback.message.answer(
            format_pending_profile_queue_item(
                item,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_moderator_open_profile_btn",
                                language,
                            ).format(number=number),
                            callback_data=(
                                f"SA_RO_MOD_PROFILE:{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_moderator_queue_keyboard(
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_MOD_PROFILE:")
)
async def super_admin_read_only_moderator_profile(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    profile_ids = data.get(
        "super_admin_impersonation_moderator_profile_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
        or index < 0
        or index >= len(profile_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        specialist_id = UUID(profile_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_moderator_specialist_card(
                moderator_user_id=target_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_moderator_page"
        ) or 0
    )

    await callback.message.answer(
        format_pending_specialist_card(
            card,
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=f"SA_RO_MOD_QUEUE:{page}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("SA_RO_MOD_COMPLAINTS:")
)
async def super_admin_read_only_moderator_complaints(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        _, view, raw_page = (
            callback.data or ""
        ).split(":", 2)
        page = max(int(raw_page), 0)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    statuses_by_view = {
        "open": {"new", "in_review"},
        "new": {"new"},
        "in_review": {"in_review"},
        "resolved": {"resolved"},
        "rejected": {"rejected"},
    }

    if view not in statuses_by_view:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            results = await ModerationService(
                ModerationRepository(session)
            ).open_complaints_queue(
                moderator_user_id=target_user_id,
                tenant_id=tenant_id,
                statuses=statuses_by_view[view],
                page=page,
                page_size=MODERATOR_PROFILE_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    cards = results[:MODERATOR_PROFILE_PAGE_SIZE]
    has_next = len(results) > MODERATOR_PROFILE_PAGE_SIZE

    await state.update_data(
        super_admin_impersonation_moderator_complaint_ids=[
            str(card.complaint_id)
            for card in cards
        ],
        super_admin_impersonation_moderator_complaint_view=view,
        super_admin_impersonation_moderator_complaint_page=page,
    )

    view_labels = {
        "open": t("moderator_complaint_filter_open", language),
        "new": t("moderator_complaint_filter_new", language),
        "in_review": t(
            "moderator_complaint_filter_review",
            language,
        ),
        "resolved": t(
            "moderator_complaint_filter_resolved",
            language,
        ),
        "rejected": t(
            "moderator_complaint_filter_rejected",
            language,
        ),
    }

    await callback.message.answer(
        t(
            "super_admin_ro_moderator_complaints_title",
            language,
        ).format(
            view=view_labels[view],
            page=page + 1,
            count=len(cards),
        )
    )

    start_number = page * MODERATOR_PROFILE_PAGE_SIZE + 1

    for index, card in enumerate(cards):
        number = start_number + index

        await callback.message.answer(
            format_complaint_queue_item(
                card,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_moderator_open_complaint_btn",
                                language,
                            ).format(number=number),
                            callback_data=(
                                f"SA_RO_MOD_COMPLAINT:{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_moderator_complaints_keyboard(
                view=view,
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_MOD_COMPLAINT:")
)
async def super_admin_read_only_moderator_complaint(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    complaint_ids = data.get(
        "super_admin_impersonation_moderator_complaint_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
        or index < 0
        or index >= len(complaint_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        complaint_id = UUID(complaint_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_moderator_complaint_card(
                moderator_user_id=target_user_id,
                tenant_id=tenant_id,
                complaint_id=complaint_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_moderator_complaint_page"
        ) or 0
    )
    view = str(
        data.get(
            "super_admin_impersonation_moderator_complaint_view"
        ) or "open"
    )

    await callback.message.answer(
        format_complaint_card(
            card,
            index=index,
            total=len(complaint_ids),
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_moderator_back_to_complaints_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_MOD_COMPLAINTS:{view}:{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    (F.data == "SA_RO_MOD_PORTFOLIO")
    | F.data.startswith("SA_RO_MOD_PORT_QUEUE:")
)
async def super_admin_read_only_moderator_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    if callback.data == "SA_RO_MOD_PORTFOLIO":
        page = 0
    else:
        try:
            page = max(
                int((callback.data or "").split(":", 1)[1]),
                0,
            )
        except (IndexError, TypeError, ValueError):
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
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
                moderator_user_id=target_user_id,
                page=page,
                page_size=MODERATOR_PORTFOLIO_PAGE_SIZE,
            )
    except PortfolioServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = items[:MODERATOR_PORTFOLIO_PAGE_SIZE]
    has_next_page = (
        len(items) > MODERATOR_PORTFOLIO_PAGE_SIZE
    )

    await state.update_data(
        super_admin_impersonation_moderator_portfolio_ids=[
            str(view.item.id)
            for view in visible_items
        ],
        super_admin_impersonation_moderator_portfolio_page=page,
        super_admin_impersonation_moderator_portfolio_has_next=(
            has_next_page
        ),
    )

    await callback.message.answer(
        t(
            "super_admin_ro_moderator_portfolio_title",
            language,
        ).format(
            page=page + 1,
            count=len(visible_items),
        )
    )

    if not visible_items:
        await callback.message.answer(
            t("admin_no_pending_portfolio", language),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_moderator_back_btn",
                                language,
                            ),
                            callback_data="SA_RO_MOD_HOME",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_impersonation_stop_btn",
                                language,
                            ),
                            callback_data="SA_IMPERSONATE_STOP",
                        )
                    ],
                ]
            ),
        )
        await callback.answer()
        return

    await show_super_admin_read_only_portfolio_item(
        callback,
        state,
        index=0,
    )


@admin_router.callback_query(
    F.data.startswith("SA_RO_MOD_PORT_VIEW:")
)
async def super_admin_read_only_moderator_portfolio_view(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await show_super_admin_read_only_portfolio_item(
        callback,
        state,
        index=index,
    )


async def show_super_admin_read_only_portfolio_item(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    index: int,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    portfolio_ids = data.get(
        "super_admin_impersonation_moderator_portfolio_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
        or not portfolio_ids
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    index = max(0, min(index, len(portfolio_ids) - 1))

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        item_id = UUID(portfolio_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_moderator_portfolio_page"
        ) or 0
    )
    has_next_page = bool(
        data.get(
            "super_admin_impersonation_moderator_portfolio_has_next"
        )
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
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
                moderator_user_id=target_user_id,
                page=page,
                page_size=MODERATOR_PORTFOLIO_PAGE_SIZE,
            )
    except PortfolioServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    view = next(
        (
            candidate
            for candidate in items[
                :MODERATOR_PORTFOLIO_PAGE_SIZE
            ]
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
        page=page,
        language=language,
    )
    keyboard = (
        super_admin_read_only_moderator_portfolio_keyboard(
            index=index,
            total=len(portfolio_ids),
            page=page,
            has_next_page=has_next_page,
            signed_url=view.signed_url,
            language=language,
        )
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

@admin_router.callback_query(F.data == "SA_RO_MOD_REVIEWS")
@admin_router.callback_query(
    F.data.startswith("SA_RO_MOD_REVIEWS_PAGE:")
)
async def super_admin_read_only_moderator_reviews(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    if callback.data == "SA_RO_MOD_REVIEWS":
        page = 0
    else:
        try:
            page = max(
                int((callback.data or "").split(":", 1)[1]),
                0,
            )
        except (IndexError, TypeError, ValueError):
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            results = await ReviewService(
                ReviewRepository(session)
            ).list_pending_reviews(
                tenant_id=tenant_id,
                moderator_user_id=target_user_id,
                page=page,
                page_size=MODERATOR_PROFILE_PAGE_SIZE,
            )
    except ReviewServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    reviews = results[:MODERATOR_PROFILE_PAGE_SIZE]
    has_next = len(results) > MODERATOR_PROFILE_PAGE_SIZE

    await state.update_data(
        super_admin_impersonation_moderator_review_ids=[
            str(review.id)
            for review in reviews
        ],
        super_admin_impersonation_moderator_review_page=page,
        super_admin_impersonation_moderator_review_has_next=(
            has_next
        ),
    )

    await callback.message.answer(
        t(
            "super_admin_ro_moderator_reviews_title",
            language,
        ).format(
            page=page + 1,
            count=len(reviews),
        )
    )

    if not reviews:
        await callback.message.answer(
            t("admin_no_pending_reviews", language),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_moderator_back_btn",
                                language,
                            ),
                            callback_data="SA_RO_MOD_HOME",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_impersonation_stop_btn",
                                language,
                            ),
                            callback_data="SA_IMPERSONATE_STOP",
                        )
                    ],
                ]
            ),
        )
        await callback.answer()
        return

    await show_super_admin_read_only_review(
        callback,
        state,
        index=0,
    )


@admin_router.callback_query(
    F.data.startswith("SA_RO_MOD_REV_VIEW:")
)
async def super_admin_read_only_moderator_review_view(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await show_super_admin_read_only_review(
        callback,
        state,
        index=index,
    )


async def show_super_admin_read_only_review(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    index: int,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    review_ids = data.get(
        "super_admin_impersonation_moderator_review_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
        or not review_ids
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    index = max(0, min(index, len(review_ids) - 1))

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        review_id = UUID(review_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_moderator_review_page"
        ) or 0
    )
    has_next = bool(
        data.get(
            "super_admin_impersonation_moderator_review_has_next"
        )
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ReviewService(
                ReviewRepository(session)
            ).get_pending_review_for_moderation(
                tenant_id=tenant_id,
                moderator_user_id=target_user_id,
                review_id=review_id,
            )
    except ReviewServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_review_card(
            card,
            index=index,
            total=len(review_ids),
            language=language,
        ),
        reply_markup=(
            super_admin_read_only_moderator_reviews_keyboard(
                index=index,
                total=len(review_ids),
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("SA_RO_MOD_BLACKLIST:")
)
async def super_admin_read_only_moderator_blacklist(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        _, view, raw_page = (
            callback.data or ""
        ).split(":", 2)
        page = max(int(raw_page), 0)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if view not in {"active", "revoked"}:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) not in READ_ONLY_MODERATION_TARGET_ROLES
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            cards = await ModerationService(
                ModerationRepository(session)
            ).open_scoped_blacklist_queue(
                moderator_user_id=target_user_id,
                tenant_id=tenant_id,
                view=view,
                page=page,
                page_size=MODERATOR_PROFILE_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_cards = cards[:MODERATOR_PROFILE_PAGE_SIZE]
    has_next = len(cards) > MODERATOR_PROFILE_PAGE_SIZE

    view_label = t(
        (
            "moderator_blacklist_history_title"
            if view == "revoked"
            else "moderator_blacklist_active_title"
        ),
        language,
    )

    await callback.message.answer(
        t(
            "super_admin_ro_moderator_blacklist_title",
            language,
        ).format(
            view=view_label,
            page=page + 1,
            count=len(visible_cards),
        )
    )

    if visible_cards:
        start_number = page * MODERATOR_PROFILE_PAGE_SIZE + 1

        for offset, card in enumerate(visible_cards):
            await callback.message.answer(
                format_scoped_blacklist_card(
                    card,
                    number=start_number + offset,
                    language=language,
                )
            )
    else:
        await callback.message.answer(
            t("moderator_blacklist_empty", language)
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_moderator_blacklist_keyboard(
                view=view,
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
    await callback.answer()

async def show_super_admin_admin_read_only_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            summary = await ModerationService(
                ModerationRepository(session)
            ).open_admin_menu(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    user_number = (
        data.get(
            "super_admin_impersonation_target_user_number"
        )
        or t(
            "super_admin_value_not_specified",
            language,
        )
    )

    text = (
        f"{t(
            'super_admin_impersonation_admin_cabinet',
            language,
        ).format(
            user_number=user_number,
        )}\n\n"
        f"{format_admin_menu(
            summary,
            language,
        )}"
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=text,
        reply_markup=(
            super_admin_read_only_admin_menu_keyboard(
                language
            )
        ),
    )


@admin_router.callback_query(F.data == "SA_RO_ADMIN_HOME")
async def super_admin_read_only_admin_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_super_admin_admin_read_only_cabinet(
        callback,
        state,
    )


@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_AUDIT_QUEUE:")
)
async def super_admin_read_only_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        _, target_type, raw_page = (
            callback.data or ""
        ).split(":", 2)
        page = max(int(raw_page), 0)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).open_admin_audit(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
                target_type=target_type,
                page=page,
                page_size=ADMIN_AUDIT_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_admin_audit_action_ids=[
            str(card.action_id)
            for card in result.items
        ],
        super_admin_impersonation_admin_audit_target_type=(
            result.target_type
        ),
        super_admin_impersonation_admin_audit_page=result.page,
    )

    await callback.message.answer(
        t(
            "super_admin_ro_admin_audit_title",
            language,
        ).format(
            target_type=result.target_type,
            page=result.page + 1,
            count=len(result.items),
        )
    )

    if not result.items:
        await callback.message.answer(
            t("admin_audit_empty", language),
            reply_markup=admin_audit_queue_keyboard(
                target_type=result.target_type,
                page=result.page,
                has_next=False,
                language=language,
                prefix="SA_RO_ADMIN_AUDIT",
                back_callback="SA_RO_ADMIN_HOME",
            ),
        )
        await callback.answer()
        return

    start_number = result.page * ADMIN_AUDIT_PAGE_SIZE + 1

    for index, card in enumerate(result.items):
        await callback.message.answer(
            format_admin_audit_card(
                card,
                number=start_number + index,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "admin_audit_open_btn",
                                language,
                            ),
                            callback_data=(
                                f"SA_RO_ADMIN_AUDIT_OPEN:{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=admin_audit_queue_keyboard(
            target_type=result.target_type,
            page=result.page,
            has_next=result.has_next,
            language=language,
            prefix="SA_RO_ADMIN_AUDIT",
            back_callback="SA_RO_ADMIN_HOME",
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "SA_RO_ADMIN_AUDIT_FILTER"
)
async def super_admin_read_only_admin_audit_filter(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("admin_audit_filter_title", language),
        reply_markup=(
            super_admin_read_only_admin_audit_filter_keyboard(
                language
            )
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_AUDIT_OPEN:")
)
async def super_admin_read_only_admin_audit_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    action_ids = data.get(
        "super_admin_impersonation_admin_audit_action_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(action_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        action_id = UUID(action_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_admin_audit_card(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
                action_id=action_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    target_type = str(
        data.get(
            "super_admin_impersonation_admin_audit_target_type"
        ) or "all"
    )
    page = int(
        data.get(
            "super_admin_impersonation_admin_audit_page"
        ) or 0
    )

    await callback.message.answer(
        t("admin_audit_details", language).format(
            date=card.date,
            actor=card.actor,
            action=card.action,
            target=card.target,
            target_type=card.target_type,
            reason=card.reason,
            source=card.source,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_admin_back_to_audit_btn",
                            language,
                        ),
                        callback_data=(
                            "SA_RO_ADMIN_AUDIT_QUEUE:"
                            f"{target_type}:{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_GLOBAL_BLACKLIST:")
)
async def super_admin_read_only_admin_global_blacklist(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        _, view, raw_page = (
            callback.data or ""
        ).split(":", 2)
        page = max(int(raw_page), 0)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if view not in {"active", "history"}:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).open_global_blacklist_queue(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
                view=view,
                page=page,
                page_size=ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    view_label = t(
        (
            "admin_global_blacklist_history_title"
            if result.view == "history"
            else "admin_global_blacklist_active_title"
        ),
        language,
    )

    await callback.message.answer(
        t(
            "super_admin_ro_admin_global_blacklist_title",
            language,
        ).format(
            view=view_label,
            page=result.page + 1,
            count=len(result.items),
        )
    )

    if result.items:
        start_number = (
            result.page * ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE
            + 1
        )

        for offset, card in enumerate(result.items):
            await callback.message.answer(
                format_global_blacklist_card(
                    card,
                    number=start_number + offset,
                    language=language,
                )
            )
    else:
        await callback.message.answer(
            t("admin_global_blacklist_empty", language)
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_admin_global_blacklist_keyboard(
                view=result.view,
                page=result.page,
                has_next=result.has_next,
                language=language,
            )
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_SUPPORT:")
)
async def super_admin_read_only_admin_support(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int((callback.data or "").split(":", 1)[1]),
            0,
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            ticket_page = await SupportService(
                SupportRepository(session)
            ).list_admin_escalated_tickets(
                tenant_id=tenant_id,
                admin_user_id=target_user_id,
                page=page,
                page_size=ADMIN_ESCALATED_TICKET_PAGE_SIZE,
            )
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_admin_support_ticket_ids=[
            str(ticket.id)
            for ticket in ticket_page.tickets
        ],
        super_admin_impersonation_admin_support_page=(
            ticket_page.page
        ),
    )

    await callback.message.answer(
        t("admin_escalated_tickets_header", language).format(
            page=ticket_page.page + 1,
            count=len(ticket_page.tickets),
        )
    )

    if not ticket_page.tickets:
        await callback.message.answer(
            t("admin_escalated_tickets_empty", language),
            reply_markup=(
                super_admin_read_only_admin_support_keyboard(
                    page=ticket_page.page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        await callback.answer()
        return

    for index, ticket in enumerate(ticket_page.tickets):
        number = (
            ticket_page.page
            * ADMIN_ESCALATED_TICKET_PAGE_SIZE
            + index
            + 1
        )

        await callback.message.answer(
            format_admin_escalated_ticket(
                ticket,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("admin_user_open_btn", language),
                            callback_data=(
                                f"SA_RO_ADMIN_SUPPORT_OPEN:{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_admin_support_keyboard(
                page=ticket_page.page,
                has_next=ticket_page.has_next,
                language=language,
            )
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_SUPPORT_OPEN:")
)
async def super_admin_read_only_admin_support_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    ticket_ids = data.get(
        "super_admin_impersonation_admin_support_ticket_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(ticket_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        ticket_id = UUID(ticket_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            ticket_view = await SupportService(
                SupportRepository(session)
            ).get_admin_escalated_ticket_view(
                tenant_id=tenant_id,
                admin_user_id=target_user_id,
                ticket_id=ticket_id,
            )
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_admin_support_page"
        ) or 0
    )

    await callback.message.answer(
        format_support_ticket_card(
            ticket_view,
            index=index,
            total=len(ticket_ids),
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_admin_back_to_tickets_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_ADMIN_SUPPORT:{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_SPECIALISTS:")
)
async def super_admin_read_only_admin_specialists(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int((callback.data or "").split(":", 1)[1]),
            0,
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            specialist_page = await ModerationService(
                ModerationRepository(session)
            ).open_admin_specialists(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
                status="all",
                page=page,
                page_size=ADMIN_SPECIALIST_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_admin_specialist_ids=[
            str(item.specialist_id)
            for item in specialist_page.items
        ],
        super_admin_impersonation_admin_specialist_page=(
            specialist_page.page
        ),
    )

    await callback.message.answer(
        t(
            "super_admin_ro_admin_specialists_title",
            language,
        ).format(
            page=specialist_page.page + 1,
            count=len(specialist_page.items),
        )
    )

    if not specialist_page.items:
        await callback.message.answer(
            t("admin_specialists_empty", language),
            reply_markup=(
                super_admin_read_only_admin_specialists_keyboard(
                    page=specialist_page.page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        await callback.answer()
        return

    start_number = (
        specialist_page.page * ADMIN_SPECIALIST_PAGE_SIZE
        + 1
    )

    for index, item in enumerate(specialist_page.items):
        number = start_number + index

        await callback.message.answer(
            format_admin_specialist_item(
                item,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_admin_open_specialist_btn",
                                language,
                            ).format(number=number),
                            callback_data=(
                                f"SA_RO_ADMIN_SPECIALIST_OPEN:{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_admin_specialists_keyboard(
                page=specialist_page.page,
                has_next=specialist_page.has_next,
                language=language,
            )
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_SPECIALIST_OPEN:")
)
async def super_admin_read_only_admin_specialist_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    specialist_ids = data.get(
        "super_admin_impersonation_admin_specialist_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(specialist_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        specialist_id = UUID(specialist_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_moderator_specialist_card(
                moderator_user_id=target_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_admin_specialist_page"
        ) or 0
    )

    await callback.message.answer(
        format_pending_specialist_card(
            card,
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_admin_back_to_specialists_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_ADMIN_SPECIALISTS:{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "SA_RO_ADMIN_USERS")
async def super_admin_read_only_admin_users_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .entering_super_admin_impersonation_admin_user_search
    )

    await callback.message.answer(
        t(
            "super_admin_ro_admin_user_search_prompt",
            language,
        ),
        reply_markup=(
            super_admin_read_only_admin_user_search_keyboard(
                language
            )
        ),
    )
    await callback.answer()


@admin_router.message(
    AdminModerationFSM
    .entering_super_admin_impersonation_admin_user_search
)
async def super_admin_read_only_admin_users_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    query = (message.text or "").strip()
    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await state.set_state(None)
        await message.answer(t("admin_access_denied", language))
        return

    if not query:
        await message.answer(
            t(
                "super_admin_ro_admin_user_search_prompt",
                language,
            )
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await state.set_state(None)
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(message.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.set_state(None)
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            cards = await ModerationService(
                ModerationRepository(session)
            ).search_admin_users(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
                query=query,
            )
    except ModerationError as exc:
        await message.answer(
            t("admin_user_search_error", language).format(
                error=str(exc),
            )
        )
        return

    await state.set_state(None)
    await state.update_data(
        super_admin_impersonation_admin_user_search_ids=[
            str(card.user_id)
            for card in cards
        ],
    )

    if not cards:
        await message.answer(
            t("admin_user_search_empty", language),
            reply_markup=(
                super_admin_read_only_admin_user_search_keyboard(
                    language
                )
            ),
        )
        return

    await message.answer(
        t("admin_user_search_results", language).format(
            count=len(cards),
        )
    )

    for index, card in enumerate(cards):
        await message.answer(
            format_admin_user_search_card(
                card,
                number=index + 1,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "admin_user_open_btn",
                                language,
                            ),
                            callback_data=(
                                f"SA_RO_ADMIN_USER_OPEN:{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_admin_user_search_keyboard(
                language
            )
        ),
    )


@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_USER_OPEN:")
)
async def super_admin_read_only_admin_user_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get(
        "super_admin_impersonation_admin_user_search_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(user_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        selected_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_admin_user_details(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
                target_user_id=selected_user_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_admin_user_details(card, language),
        reply_markup=(
            super_admin_read_only_admin_user_details_keyboard(
                index=index,
                language=language,
            )
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_USER_ROLES:")
)
async def super_admin_read_only_admin_user_roles(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get(
        "super_admin_impersonation_admin_user_search_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(user_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        selected_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_admin_user_details(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
                target_user_id=selected_user_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_admin_user_roles(card, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_back_to_card_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_ADMIN_USER_OPEN:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_ADMIN_USER_HISTORY:")
)
async def super_admin_read_only_admin_user_history(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get(
        "super_admin_impersonation_admin_user_search_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
        or index < 0
        or index >= len(user_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        selected_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            history = await ModerationService(
                ModerationRepository(session)
            ).list_admin_user_history(
                admin_user_id=target_user_id,
                tenant_id=tenant_id,
                target_user_id=selected_user_id,
                limit=10,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("admin_user_history_title", language).format(
            user_number=f"user-{selected_user_id.hex[:8]}",
            count=len(history),
        )
    )

    if history:
        for number, card in enumerate(history, start=1):
            await callback.message.answer(
                format_admin_user_history_item(
                    card,
                    number=number,
                    language=language,
                )
            )
    else:
        await callback.message.answer(
            t("admin_user_history_empty", language)
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_back_to_card_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_ADMIN_USER_OPEN:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "SA_RO_ADMIN_MODERATION"
)
async def super_admin_read_only_admin_moderation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "admin"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await show_super_admin_moderator_read_only_cabinet(
        callback,
        state,
    )

@admin_router.callback_query(F.data == "SA_RO_CLIENT_HOME")
async def super_admin_read_only_client_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_super_admin_client_read_only_cabinet(
        callback,
        state,
    )

@admin_router.callback_query(
    F.data.startswith("SA_RO_CLIENT_REQUEST")
    | F.data.startswith("SA_RO_SPECIALIST_REQUEST")
)
async def block_legacy_read_only_request_callbacks(
    callback: CallbackQuery,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await callback.answer(
        t(
            "legacy_requests_unavailable",
            language,
        ),
        show_alert=True,
    )

@admin_router.callback_query(
    F.data.startswith("SA_RO_CLIENT_DIALOGS:")
)
async def super_admin_read_only_client_dialogs(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int((callback.data or "").split(":", 1)[1]),
            0,
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "client"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, _, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await ContactChatService(
                ContactChatRepository(session)
            ).list_client_threads(
                user_id=target_user_id,
                view="active",
                limit=READ_ONLY_CLIENT_PAGE_SIZE + 1,
                offset=page * READ_ONLY_CLIENT_PAGE_SIZE,
                language=language,
            )
    except ContactChatError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = items[:READ_ONLY_CLIENT_PAGE_SIZE]
    has_next = len(items) > READ_ONLY_CLIENT_PAGE_SIZE

    await state.update_data(
        super_admin_impersonation_client_thread_ids=[
            str(item.thread_id)
            for item in visible_items
        ],
        super_admin_impersonation_client_dialogs_page=page,
    )

    await callback.message.answer(
        t(
            "super_admin_ro_client_dialogs_title",
            language,
        ).format(
            page=page + 1,
            count=len(visible_items),
        )
    )

    if not visible_items:
        await callback.message.answer(
            t("client_dialogs_empty", language),
            reply_markup=(
                super_admin_read_only_client_dialogs_keyboard(
                    page=page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        await callback.answer()
        return

    start_number = page * READ_ONLY_CLIENT_PAGE_SIZE + 1

    for index, item in enumerate(visible_items):
        number = start_number + index

        await callback.message.answer(
            format_super_admin_read_only_client_dialog(
                item,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_client_open_dialog_btn",
                                language,
                            ).format(number=number),
                            callback_data=(
                                f"SA_RO_CLIENT_DIALOG_OPEN:{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_client_dialogs_keyboard(
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_CLIENT_DIALOG_OPEN:")
)
async def super_admin_read_only_client_dialog_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    thread_ids = data.get(
        "super_admin_impersonation_client_thread_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "client"
        or index < 0
        or index >= len(thread_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        thread_id = UUID(thread_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, _, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            detail = await ContactChatService(
                ContactChatRepository(session)
            ).get_thread_detail(
                thread_id=thread_id,
                user_id=target_user_id,
                language=language,
            )
    except ContactChatError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_client_dialogs_page"
        ) or 0
    )

    await callback.message.answer(
        format_super_admin_read_only_client_dialog_detail(
            detail,
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_client_back_to_dialogs_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_CLIENT_DIALOGS:{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "SA_RO_SPECIALIST_PROFILE"
)
async def super_admin_read_only_specialist_profile(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "specialist"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )

    try:
        target_user_id = UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        profile = await SpecialistService(
            SpecialistRepository(session)
        ).get_read_only_public_profile(
            user_id=target_user_id,
            language=language,
        )

    if not profile:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t(
            "super_admin_ro_specialist_profile",
            language,
        ).format(
            display_name=profile.display_name,
            professions=(
                ", ".join(profile.professions)
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            location=profile.location,
            description=(
                profile.short_description
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            status=super_admin_preview_status_label(
                profile.status,
                language,
            ),
            availability=super_admin_preview_availability_label(
                profile.is_available,
                language,
            ),
        ),
        reply_markup=super_admin_read_only_specialist_menu_keyboard(
            language
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "SA_RO_SPECIALIST_HOME")
async def super_admin_read_only_specialist_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_super_admin_specialist_read_only_cabinet(
        callback,
        state,
    )


@admin_router.callback_query(
    F.data.startswith("SA_RO_SPECIALIST_DIALOGS:")
)
async def super_admin_read_only_specialist_dialogs(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            int((callback.data or "").split(":", 1)[1]),
            0,
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "specialist"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, _, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await ContactChatService(
                ContactChatRepository(session)
            ).list_specialist_threads(
                user_id=target_user_id,
                view="active",
                limit=READ_ONLY_CLIENT_PAGE_SIZE + 1,
                offset=page * READ_ONLY_CLIENT_PAGE_SIZE,
                language=language,
            )
    except ContactChatError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_items = items[:READ_ONLY_CLIENT_PAGE_SIZE]
    has_next = len(items) > READ_ONLY_CLIENT_PAGE_SIZE

    await state.update_data(
        super_admin_impersonation_specialist_thread_ids=[
            str(item.thread_id)
            for item in visible_items
        ],
        super_admin_impersonation_specialist_dialogs_page=page,
    )

    await callback.message.answer(
        t(
            "super_admin_ro_specialist_dialogs_title",
            language,
        ).format(
            page=page + 1,
            count=len(visible_items),
        )
    )

    if not visible_items:
        await callback.message.answer(
            t("client_dialogs_empty", language),
            reply_markup=(
                super_admin_read_only_specialist_dialogs_keyboard(
                    page=page,
                    has_next=False,
                    language=language,
                )
            ),
        )
        await callback.answer()
        return

    start_number = page * READ_ONLY_CLIENT_PAGE_SIZE + 1

    for index, item in enumerate(visible_items):
        number = start_number + index

        await callback.message.answer(
            format_super_admin_read_only_specialist_dialog(
                item,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_specialist_open_dialog_btn",
                                language,
                            ).format(number=number),
                            callback_data=(
                                "SA_RO_SPECIALIST_DIALOG_OPEN:"
                                f"{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=(
            super_admin_read_only_specialist_dialogs_keyboard(
                page=page,
                has_next=has_next,
                language=language,
            )
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_SPECIALIST_DIALOG_OPEN:")
)
async def super_admin_read_only_specialist_dialog_open(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    thread_ids = data.get(
        "super_admin_impersonation_specialist_thread_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "specialist"
        or index < 0
        or index >= len(thread_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        thread_id = UUID(thread_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, _, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            detail = await ContactChatService(
                ContactChatRepository(session)
            ).get_thread_detail(
                thread_id=thread_id,
                user_id=target_user_id,
                language=language,
            )
    except ContactChatError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_specialist_dialogs_page"
        ) or 0
    )

    await callback.message.answer(
        format_super_admin_read_only_specialist_dialog_detail(
            detail,
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_specialist_back_to_dialogs_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_SPECIALIST_DIALOGS:{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "SA_RO_SUPPORT_HOME")
async def super_admin_read_only_support_home(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_super_admin_support_read_only_cabinet(
        callback,
        state,
    )


@admin_router.callback_query(
    F.data.startswith("SA_RO_SUPPORT_LIST:")
)
async def super_admin_read_only_support_list(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        _, view, raw_page = (
            callback.data or ""
        ).split(":", 2)
        page = max(int(raw_page), 0)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if view not in {"open", "in_progress", "resolved"}:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "support"
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )

    try:
        target_user_id = UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            tickets = await SupportService(
                SupportRepository(session)
            ).list_staff_tickets(
                tenant_id=tenant_id,
                staff_user_id=target_user_id,
                statuses={view},
                limit=SUPPORT_STAFF_PAGE_SIZE + 1,
                offset=page * SUPPORT_STAFF_PAGE_SIZE,
            )
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    visible_tickets = tickets[:SUPPORT_STAFF_PAGE_SIZE]
    has_next = len(tickets) > SUPPORT_STAFF_PAGE_SIZE

    await state.update_data(
        super_admin_impersonation_support_ticket_ids=[
            str(ticket.id)
            for ticket in visible_tickets
        ],
        super_admin_impersonation_support_view=view,
        super_admin_impersonation_support_page=page,
    )

    await callback.message.answer(
        t(
            "super_admin_ro_support_list_title",
            language,
        ).format(
            view=support_staff_view_label(view, language),
            page=page + 1,
            count=len(visible_tickets),
        )
    )

    start_number = page * SUPPORT_STAFF_PAGE_SIZE + 1

    for index, ticket in enumerate(visible_tickets):
        number = start_number + index

        await callback.message.answer(
            format_super_admin_read_only_support_ticket(
                ticket,
                number=number,
                language=language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "super_admin_ro_support_open_ticket_btn",
                                language,
                            ).format(number=number),
                            callback_data=(
                                f"SA_RO_SUPPORT_OPEN:{index}"
                            ),
                        )
                    ]
                ]
            ),
        )

    await callback.message.answer(
        t("super_admin_ro_read_only_label", language),
        reply_markup=super_admin_read_only_support_list_keyboard(
            view=view,
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("SA_RO_SUPPORT_OPEN:")
)
async def super_admin_read_only_support_open_ticket(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    ticket_ids = data.get(
        "super_admin_impersonation_support_ticket_ids"
    ) or []

    if (
        not data.get("super_admin_impersonation_read_only")
        or data.get(
            "super_admin_impersonation_target_role"
        ) != "support"
        or index < 0
        or index >= len(ticket_ids)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            str(
                data.get(
                    "super_admin_impersonation_target_user_id"
                )
            )
        )
        ticket_id = UUID(ticket_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            ticket_view = await SupportService(
                SupportRepository(session)
            ).get_staff_ticket_view(
                tenant_id=tenant_id,
                staff_user_id=target_user_id,
                ticket_id=ticket_id,
            )
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    page = int(
        data.get(
            "super_admin_impersonation_support_page"
        ) or 0
    )
    view = str(
        data.get(
            "super_admin_impersonation_support_view"
        ) or "open"
    )
    number = page * SUPPORT_STAFF_PAGE_SIZE + index + 1

    await callback.message.answer(
        format_super_admin_read_only_support_ticket_detail(
            ticket_view,
            number=number,
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_ro_support_back_to_list_btn",
                            language,
                        ),
                        callback_data=(
                            f"SA_RO_SUPPORT_LIST:{view}:{page}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_stop_btn",
                            language,
                        ),
                        callback_data="SA_IMPERSONATE_STOP",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

async def show_super_admin_client_read_only_cabinet(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()
    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )
    target_role = data.get(
        "super_admin_impersonation_target_role"
    )
    is_read_only = bool(
        data.get("super_admin_impersonation_read_only")
    )

    if (
        not target_user_id_raw
        or target_role != "client"
        or not is_read_only
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(str(target_user_id_raw))
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            cabinet = await ModerationService(
                ModerationRepository(session)
            ).get_client_read_only_cabinet(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                language=language,
            )

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_client_cabinet",
            language,
        ).format(
            user_number=cabinet.user_number,
            display_name=(
                cabinet.display_name
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            city_name=(
                cabinet.city_name
                or t(
                    "super_admin_value_not_specified",
                    language,
                )
            ),
            dialogs_unread=cabinet.dialogs_unread,
        ),
        reply_markup=(
            super_admin_read_only_client_menu_keyboard(
                language
            )
        ),
    )

@admin_router.callback_query(
    F.data == "SA_IMPERSONATE_MENU"
)
async def super_admin_impersonation_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    data = await state.get_data()

    if not data.get(
        "super_admin_impersonation_read_only"
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_menu",
            language,
        ),
        reply_markup=super_admin_impersonation_keyboard(
            language
        ),
    )

@admin_router.callback_query(F.data == "SA_USER_IMPERSONATE")
async def super_admin_impersonation_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    if not data.get("super_admin_selected_user_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_super_admin_impersonation_reason
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_reason_prompt",
            language,
        ),
    )


@admin_router.message(
    AdminModerationFSM.entering_super_admin_impersonation_reason
)
async def super_admin_impersonation_reason_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "super_admin_role_bad_format",
                language,
            ),
        )
        return

    await state.update_data(
        super_admin_impersonation_reason=reason,
    )
    await state.set_state(None)

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "super_admin_impersonation_menu",
            language,
        ),
        reply_markup=super_admin_impersonation_keyboard(
            language
        ),
    )

@admin_router.callback_query(F.data.startswith("SA_IMPERSONATE_ROLE:"))
async def super_admin_impersonation_role(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        target_role = callback.data.split(":", 1)[1]
    except (IndexError, TypeError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    target_user_id_raw = data.get("super_admin_selected_user_id")
    reason = data.get("super_admin_impersonation_reason")

    if not target_user_id_raw or not reason:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(target_user_id_raw)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            preview = await ModerationService(
                ModerationRepository(session)
            ).start_super_admin_impersonation_view(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                target_role=target_role,
                reason=reason,
            )

    except ImpersonationRoleUnavailableError:
        await callback.answer(
            t(
                "super_admin_impersonation_role_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        super_admin_impersonation_target_user_id=str(
            target_user_id
        ),
        super_admin_impersonation_target_user_number=(
            preview.target_user_number
        ),
        super_admin_impersonation_target_role=(
            preview.target_role
        ),
        super_admin_impersonation_read_only=True,
    )

    if preview.target_role == "client":
        await show_super_admin_client_read_only_cabinet(
            callback,
            state,
        )
        return
    if preview.target_role == "specialist":
        await show_super_admin_specialist_read_only_cabinet(
            callback,
            state,
        )
        return
    if preview.target_role == "support":
        await show_super_admin_support_read_only_cabinet(
            callback,
            state,
        )
        return

    if preview.target_role == "moderator":
        await show_super_admin_moderator_read_only_cabinet(
            callback,
            state,
        )
        return

    if preview.target_role == "admin":
        await show_super_admin_admin_read_only_cabinet(
            callback,
            state,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_preview",
            language,
        ).format(
            user=preview.target_user_number,
            role=super_admin_user_role_label(
                preview.target_role,
                language,
            ),
        ),
        reply_markup=super_admin_impersonation_keyboard(
            language
        ),
    )


@admin_router.callback_query(F.data == "SA_IMPERSONATE_STOP")
async def super_admin_impersonation_stop(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    target_user_id_raw = data.get(
        "super_admin_impersonation_target_user_id"
    )
    reason = data.get(
        "super_admin_impersonation_reason"
    ) or "Read-only preview stopped."

    if (
        not target_user_id_raw
        or not data.get("super_admin_impersonation_read_only")
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(target_user_id_raw)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await ModerationService(
                ModerationRepository(session)
            ).stop_super_admin_impersonation_view(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                reason=reason,
            )

    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        super_admin_impersonation_reason=None,
        super_admin_impersonation_target_user_id=None,
        super_admin_impersonation_target_user_number=None,
        super_admin_impersonation_target_role=None,
        super_admin_impersonation_read_only=None,
    )
    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_impersonation_stopped",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_impersonation_to_user_btn",
                            language,
                        ),
                        callback_data="SA_USER_PROFILE",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "super_admin_scopes_to_panel_btn",
                            language,
                        ),
                        callback_data="SA_PANEL",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "main_menu",
                            language,
                        ),
                        callback_data="MAIN_MENU",
                    )
                ],
            ]
        ),
    )

def format_moderator_menu(summary, language: str) -> str:
    return t("moderator_menu_text", language).format(
        profiles=summary.profiles,
        portfolio=summary.portfolio,
        reviews=summary.reviews,
        complaints=summary.complaints,
        blacklist=summary.blacklist,
    )


def moderator_menu_keyboard(
    summary,
    language: str,
    *,
    show_role_switch: bool,
    show_specialist_management: bool = False,
    back_callback: str = "ADM_MENU",
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("moderator_profiles_btn", language).format(
                    count=summary.profiles,
                ),
                callback_data="ADM_PENDING",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("moderator_portfolio_btn", language).format(
                    count=summary.portfolio,
                ),
                callback_data="ADM_PORTFOLIO",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("moderator_reviews_btn", language).format(
                    count=summary.reviews,
                ),
                callback_data="ADM_REVIEWS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("moderator_complaints_btn", language).format(
                    count=summary.complaints,
                ),
                callback_data="ADM_COMPLAINTS",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("moderator_blacklist_btn", language).format(
                    count=summary.blacklist,
                ),
                callback_data="ADM_SCOPED_BLACKLIST",
            )
        ],
    ]
    if show_specialist_management:
        rows.insert(
            1,
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_specialist_management_btn",
                        language,
                    ),
                    callback_data="ADM_ADMIN_SPECIALISTS",
                )
            ],
        )


    if show_role_switch:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("switch_profile", language),
                    callback_data="ROLE_SWITCH_MENU",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t("moderator_back_btn", language),
                callback_data=back_callback,
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_admin_panel(
    message_or_callback,
    state: FSMContext | None = None,
    *,
    callback_answered: bool = False,
):
    user = message_or_callback.from_user
    language = normalize_language(user.language_code)

    target_message = (
        message_or_callback.message
        if isinstance(message_or_callback, CallbackQuery)
        else message_or_callback
    )
    
    async def send_panel(
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        if isinstance(
            message_or_callback,
            CallbackQuery,
        ):
            menu_message = (
                await edit_or_replace_menu_message(
                    callback=message_or_callback,
                    text=text,
                    reply_markup=reply_markup,
                )
            )

            if state:
                await state.update_data(
                    last_menu_message_id=(
                        menu_message.message_id
                    )
                )

            return menu_message

        return await target_message.answer(
            text,
            reply_markup=reply_markup,
        )


    if (
        isinstance(message_or_callback, CallbackQuery)
        and not callback_answered
    ):
        await message_or_callback.answer()
        callback_answered = True

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        user.id
    )
    if not admin_user_id or not roles:
        await send_panel(
            t("admin_access_denied", language)
        )
        return

    if state:
        state_data = await state.get_data()

        if isinstance(
            message_or_callback,
            CallbackQuery,
        ):
            await delete_telegram_messages(
                bot=message_or_callback.bot,
                chat_id=(
                    message_or_callback.message.chat.id
                ),
                message_ids=[
                    *(
                        state_data.get(
                            "admin_scope_list_message_ids"
                        )
                        or []
                    ),
                    *(
                        state_data.get(
                            "admin_global_blacklist_message_ids"
                        )
                        or []
                    ),
                    *(
                        state_data.get(
                            "admin_scoped_blacklist_message_ids"
                        )
                        or []
                    ),
                ],
            )

        await state.clear()

    async with get_session() as session:
        role_context = await UserService(session).get_role_switch_context(user.id)

    show_role_switch = bool(
        role_context and len(role_context.available_roles) > 1
    )
    active_role = UserService.resolve_staff_panel_role(
        (
            role_context.active_role
            if role_context
            else None
        ),
        roles,
    )

    admin_entry_role = (
        active_role
        if active_role in {"admin", "super_admin"}
        else None
    )

    if admin_entry_role in {"admin", "super_admin"}:
        if (
            not tenant_id
            or not roles.intersection({"admin", "super_admin"})
        ):
            await send_panel(
                t("admin_access_denied", language)
            )
            return

        try:
            async with get_session() as session:
                moderation_service = ModerationService(
                    ModerationRepository(session)
                )

                if admin_entry_role == "super_admin":
                    summary = await moderation_service.open_super_admin_menu(
                        admin_user_id=admin_user_id,
                        tenant_id=tenant_id,
                    )
                else:
                    summary = await moderation_service.open_admin_menu(
                        admin_user_id=admin_user_id,
                        tenant_id=tenant_id,
                    )
        except ModerationError as exc:
            await send_panel(str(exc))
            return

        if admin_entry_role == "super_admin":
            await send_panel(
                format_super_admin_menu(summary, language),
                reply_markup=super_admin_menu_keyboard(
                    summary,
                    language,
                    show_role_switch=show_role_switch,
),
            )
        else:
            await send_panel(
                format_admin_menu(summary, language),
                reply_markup=minimal_admin_menu_keyboard(
                    summary,
                    language,
                    show_role_switch=show_role_switch,
                ),
            )

        return

    if active_role == "support":
        if not tenant_id or "support" not in roles:
            await send_panel(
                t("admin_access_denied", language)
            )
            return
        try:
            async with get_session() as session:
                counts = await SupportService(
                    SupportRepository(session)
                ).get_staff_ticket_counts(
                    tenant_id=tenant_id,
                    staff_user_id=admin_user_id,
                    statuses={"open", "in_progress", "resolved"},
                )

                await EventRepository(session).create_event(
                    event_type="support_menu",
                    tenant_id=tenant_id,
                    user_id=admin_user_id,
                    entity_type="support_ticket",
                    entity_id=None,
                    payload={
                        "source": "support_staff_menu",
                        "counts": counts,
                    },
                    platform="telegram",
                )
                await session.commit()
        except SupportServiceError as exc:
            await send_panel(str(exc))
            return

        await send_panel(
            format_support_staff_menu(counts, language),
            reply_markup=support_staff_menu_keyboard(
                language,
                show_role_switch=show_role_switch,
            ),
        )

        return
    if active_role == "moderator":
        if not tenant_id or "moderator" not in roles:
            await send_panel(
                t("admin_access_denied", language)
            )
            return

        try:
            async with get_session() as session:
                summary = await ModerationService(
                    ModerationRepository(session)
                ).open_moderator_menu(
                    moderator_user_id=admin_user_id,
                    tenant_id=tenant_id,
                )
        except ModerationError as exc:
            await send_panel(str(exc))
            return

        await send_panel(
            format_moderator_menu(summary, language),
            reply_markup=moderator_menu_keyboard(
                summary,
                language,
                show_role_switch=show_role_switch,
            ),
        )

        return

    panel_roles = effective_panel_roles(
        roles,
        active_role,
    )
    panel_text = t("admin_panel_title", language)

    if not (
        panel_roles.intersection(ADMIN_MODERATION_MENU_ROLES)
        or panel_roles.intersection(ADMIN_PAYMENT_MENU_ROLES)
        or panel_roles.intersection(ADMIN_ROLE_MENU_ROLES)
        or panel_roles.intersection(ADMIN_LOG_MENU_ROLES)
        or panel_roles.intersection(ADMIN_SUPPORT_MENU_ROLES)
        or panel_roles.intersection(ADMIN_SUPPORT_STATS_ROLES)
        or panel_roles.intersection(ADMIN_DICT_MENU_ROLES)
        or panel_roles.intersection(ADMIN_DIALOGS_MENU_ROLES)
        or panel_roles.intersection(ADMIN_PROMOTION_MENU_ROLES)
        or panel_roles.intersection(ADMIN_SYSTEM_MENU_ROLES)
    ):
        panel_text = t("admin_no_available_actions", language)

    await send_panel(
        panel_text,
        reply_markup=admin_panel_keyboard(
            language,
            panel_roles,
            show_role_switch=show_role_switch,
        ),
    )

@admin_router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    await show_admin_panel(message, state)


@admin_router.callback_query(F.data == "ADM_PANEL")
async def admin_panel_callback(callback: CallbackQuery, state: FSMContext):
    await show_admin_panel(callback, state)

@admin_router.callback_query(F.data == "ADM_DICT")
async def admin_dictionaries_menu(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("admin_dict_menu_title", language),
        reply_markup=admin_dictionaries_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_DICT_CATEGORIES")
@admin_router.callback_query(F.data.startswith("ADM_DICT_CATEGORIES:"))
async def admin_categories_dictionary(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        items = await DictionaryService(
            DictionaryRepository(session)
        ).list_category_cards(
            language=language,
            limit=ADMIN_CATEGORIES_PAGE_SIZE + 1,
            offset=page * ADMIN_CATEGORIES_PAGE_SIZE,
        )

    has_next = len(items) > ADMIN_CATEGORIES_PAGE_SIZE
    visible_items = items[:ADMIN_CATEGORIES_PAGE_SIZE]

    await state.update_data(
        admin_category_ids=[
            str(item.category_id)
            for item in visible_items
        ],
        admin_category_page=page,
    )

    await callback.message.answer(
        format_admin_categories_list(
            visible_items,
            language,
            page=page,
        ),
        reply_markup=admin_categories_list_keyboard(
            language,
            page=page,
            has_next=has_next,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_CAT_CREATE")
async def admin_category_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_category_create)
    await callback.message.answer(
        t("admin_dict_category_create_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_category_create)
async def admin_category_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).create_category(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                title=message.text or "",
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_selected_category_id=str(item.category_id),
    )
    await state.set_state(None)

    await message.answer(
        t("admin_dict_category_create_done", language),
    )
    await message.answer(
        format_admin_category_card(item, language),
        reply_markup=admin_category_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_CAT_OPEN_STUB")
async def admin_category_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    category_ids = data.get("admin_category_ids") or []

    if not category_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_category_number)

    await callback.message.answer(
        t("admin_dict_category_open_prompt", language).format(
            count=len(category_ids),
        )
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.in_(
        {
            "ADM_CAT_CREATE_STUB",
            "ADM_CAT_REORDER_STUB",
        }
    )
)
async def admin_category_action_stub(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)

    await callback.answer(
        t("feature_disabled_beta_message", language),
        show_alert=True,
    )

def format_admin_categories_list(
    items,
    language: str,
    *,
    page: int = 0,
) -> str:
    if not items:
        return t("admin_dict_categories_empty", language)

    lines = [
        t("admin_dict_categories_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_category_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                status=item.status,
                sort_order=item.sort_order,
                professions=item.professions_count,
                specialists=item.specialists_count,
                release=item.release or "-",
            )
        )

    return "\n\n".join(lines)

def format_admin_professions_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_professions_empty", language)

    lines = [
        t("admin_dict_professions_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_profession_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                category=item.category_name,
                status=item.status,
                sort_order=item.sort_order,
                specialists=item.specialists_count,
                release=item.release or "-",
            )
        )

    return "\n\n".join(lines)

def format_admin_countries_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_countries_empty", language)

    lines = [
        t("admin_dict_countries_title", language).format(
            count=len(items)
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_country_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                status=item.status,
                cities=item.cities_count,
                specialists=item.specialists_count,
            )
        )

    return "\n\n".join(lines)


def admin_countries_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_country_create_btn", language),
                callback_data="ADM_COUNTRY_CREATE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_country_import_btn", language),
                callback_data="ADM_COUNTRY_IMPORT",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_country_open_btn", language),
                callback_data="ADM_COUNTRY_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_DICT_GEO:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_GEO:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def read_admin_csv_payload_from_message(
    message: Message,
    bot: Bot,
    language: str,
) -> str | None:
    if message.text:
        return message.text

    if not message.document:
        await message.answer(t("admin_dict_import_file_required", language))
        return None

    file_name = message.document.file_name or ""

    if not file_name.lower().endswith(".csv"):
        await message.answer(t("admin_dict_import_file_invalid", language))
        return None

    file = await bot.get_file(message.document.file_id)
    buffer = await bot.download_file(file.file_path)

    if buffer is None:
        await message.answer(t("admin_dict_import_file_encoding_error", language))
        return None

    try:
        return buffer.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        await message.answer(t("admin_dict_import_file_encoding_error", language))
        return None



def format_admin_dictionary_import_result(
    result,
    language: str,
) -> str:
    errors = "\n".join(
        f"- {error}"
        for error in result.errors[:10]
    )

    if not errors:
        errors = "-"

    return t("admin_dict_import_done", language).format(
        created=result.created_count,
        updated=result.updated_count,
        skipped=result.skipped_count,
        errors=errors,
    )



def format_admin_country_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_country_card", language).format(
        title=item.title,
        code=item.code,
        status=item.status,
        default_language=item.default_language or "-",
        default_currency=item.default_currency or "-",
        phone_code=item.phone_code or "-",
        cities=item.cities_count,
        specialists=item.specialists_count,
    )


def admin_country_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_country_cities_btn", language),
                    callback_data="ADM_COUNTRY_CITIES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_create_btn", language),
                    callback_data="ADM_CITY_CREATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_import_btn", language),
                    callback_data="ADM_CITY_IMPORT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_country_update_btn", language),
                    callback_data="ADM_COUNTRY_UPDATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_country_toggle_btn", language),
                    callback_data="ADM_COUNTRY_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_GEO",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )


def format_admin_cities_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_cities_empty", language)

    lines = [
        t("admin_dict_cities_title", language).format(
            count=len(items)
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_city_row", language).format(
                number=index,
                title=item.title,
                status=item.status,
                timezone=item.timezone or "-",
                specialists=item.specialists_count,
            )
        )

    return "\n\n".join(lines)


def admin_cities_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_city_open_btn", language),
                callback_data="ADM_CITY_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_COUNTRY_CITIES:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_COUNTRY_CITIES:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_GEO",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_city_card(
    item,
    language: str,
) -> str:
    coordinates = "-"
    if item.latitude is not None and item.longitude is not None:
        coordinates = f"{item.latitude}, {item.longitude}"

    return t("admin_dict_city_card", language).format(
        title=item.title,
        country=item.country_name,
        status=item.status,
        timezone=item.timezone or "-",
        coordinates=coordinates,
        specialists=item.specialists_count,
    )


def admin_city_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_update_btn", language),
                    callback_data="ADM_CITY_UPDATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_geo_update_btn", language),
                    callback_data="ADM_CITY_GEO_UPDATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_city_toggle_btn", language),
                    callback_data="ADM_CITY_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_COUNTRY_CITIES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

def format_admin_languages_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_languages_empty", language)

    lines = [
        t("admin_dict_languages_title", language).format(
            count=len(items)
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_language_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                native_name=item.native_name or "-",
                status=item.status,
                specialist_links=item.specialist_links_count,
            )
        )

    return "\n\n".join(lines)


def admin_languages_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_language_create_btn", language),
                callback_data="ADM_LANGUAGE_CREATE",
                )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_language_open_btn", language),
                callback_data="ADM_LANGUAGE_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_DICT_LANGUAGES:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_LANGUAGES:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_language_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_language_card", language).format(
        title=item.title,
        code=item.code,
        native_name=item.native_name or "-",
        status=item.status,
        specialist_links=item.specialist_links_count,
    )


def admin_language_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_language_rename_btn", language),
                    callback_data="ADM_LANGUAGE_RENAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_language_toggle_btn", language),
                    callback_data="ADM_LANGUAGE_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_LANGUAGES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

def format_admin_skills_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_skills_empty", language)

    lines = [
        t("admin_dict_skills_title", language).format(
            count=len(items),
        )
    ]

    for index, item in enumerate(items, start=1):
        lines.append(
            t("admin_dict_skill_row", language).format(
                number=index,
                title=item.title,
                code=item.code,
                status=item.status,
                profession_links=item.profession_links_count,
                user_links=item.user_links_count,
                vacancy_links=item.vacancy_links_count,
            )
        )

    return "\n\n".join(lines)


def admin_skills_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_skill_create_btn", language),
                callback_data="ADM_SKILL_CREATE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_skill_open_btn", language),
                callback_data="ADM_SKILL_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_DICT_SKILLS:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_SKILLS:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_skill_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_skill_card", language).format(
        title=item.title,
        code=item.code,
        status=item.status,
        profession_links=item.profession_links_count,
        user_links=item.user_links_count,
        vacancy_links=item.vacancy_links_count,
    )


def admin_skill_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skill_rename_btn", language),
                    callback_data="ADM_SKILL_RENAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skill_toggle_btn", language),
                    callback_data="ADM_SKILL_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skill_merge_btn", language),
                    callback_data="ADM_SKILL_MERGE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_SKILLS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

def admin_skill_merge_confirm_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_skill_merge_confirm_btn", language),
                    callback_data="ADM_SKILL_MERGE_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_cancel", language),
                    callback_data="ADM_SKILL_MERGE_CANCEL",
                )
            ],
        ]
    )

def admin_professions_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_profession_create_btn", language),
                callback_data="ADM_PROF_CREATE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_profession_open_btn", language),
                callback_data="ADM_PROF_OPEN",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_DICT_PROFESSIONS:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_PROFESSIONS:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_profession_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_profession_card", language).format(
        title=item.title,
        code=item.code,
        category=item.category_name,
        status=item.status,
        sort_order=item.sort_order,
        specialists=item.specialists_count,
        release=item.release or "-",
    )


def admin_profession_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_rename_btn", language),
                    callback_data="ADM_PROF_RENAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_move_btn", language),
                    callback_data="ADM_PROF_MOVE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_toggle_btn", language),
                    callback_data="ADM_PROF_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_archive_btn", language),
                    callback_data="ADM_PROF_ARCHIVE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_specialists_btn", language),
                    callback_data="ADM_PROF_SPECIALISTS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_PROFESSIONS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

def admin_categories_list_keyboard(
    language: str,
    *,
    page: int = 0,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_category_create_btn", language),
                callback_data="ADM_CAT_CREATE",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_category_open_btn", language),
                callback_data="ADM_CAT_OPEN_STUB",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_category_reorder_btn", language),
                callback_data="ADM_CAT_REORDER_STUB",
            )
        ],
    ]

    paging_row = []
    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_prev", language),
                callback_data=f"ADM_DICT_CATEGORIES:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_DICT_CATEGORIES:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

@admin_router.callback_query(F.data == "ADM_DICT_PROFESSIONS")
@admin_router.callback_query(F.data.startswith("ADM_DICT_PROFESSIONS:"))
async def admin_professions_dictionary(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        items = await DictionaryService(
            DictionaryRepository(session)
        ).list_profession_cards(
            language=language,
            limit=ADMIN_PROFESSIONS_PAGE_SIZE + 1,
            offset=page * ADMIN_PROFESSIONS_PAGE_SIZE,
        )

    has_next = len(items) > ADMIN_PROFESSIONS_PAGE_SIZE
    visible_items = items[:ADMIN_PROFESSIONS_PAGE_SIZE]

    await state.update_data(
        admin_profession_ids=[
            str(item.profession_id)
            for item in visible_items
        ],
        admin_profession_page=page,
    )

    await callback.message.answer(
        format_admin_professions_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_professions_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "ADM_PROF_OPEN")
async def admin_profession_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    profession_ids = data.get("admin_profession_ids") or []

    if not profession_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_profession_number)

    await callback.message.answer(
        t("admin_dict_profession_open_prompt", language).format(
            count=len(profession_ids),
        )
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_profession_number)
async def admin_profession_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    profession_ids = data.get("admin_profession_ids") or []

    try:
        index = int((message.text or "").strip()) - 1
    except ValueError:
        await message.answer(
            t("admin_dict_profession_open_bad_number", language).format(
                count=len(profession_ids),
            )
        )
        return

    if index < 0 or index >= len(profession_ids):
        await message.answer(
            t("admin_dict_profession_open_bad_number", language).format(
                count=len(profession_ids),
            )
        )
        return

    profession_id = profession_ids[index]

    async with get_session() as session:
        item = await DictionaryService(
            DictionaryRepository(session)
        ).get_profession_card(
            profession_id=profession_id,
            language=language,
        )

    if not item:
        await message.answer(t("admin_item_not_found", language))
        await state.clear()
        return

    await state.update_data(
        admin_selected_profession_id=profession_id,
    )
    await state.set_state(None)

    await message.answer(
        format_admin_profession_card(item, language),
        reply_markup=admin_profession_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_PROF_CREATE")
async def admin_profession_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    await state.set_state(AdminModerationFSM.entering_admin_profession_create)

    if category_id:
        await callback.message.answer(
            t("admin_dict_profession_create_prompt_in_category", language)
        )
    else:
        await callback.message.answer(
            t("admin_dict_profession_create_prompt_with_category", language)
        )

    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_profession_create)
async def admin_profession_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    raw_text = message.text or ""
    category_code = None
    title = raw_text

    if not category_id:
        parts = raw_text.split("|", 1)
        if len(parts) != 2:
            await message.answer(
                t("admin_dict_profession_create_format_error", language)
            )
            return

        category_code = parts[0].strip()
        title = parts[1].strip()

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).create_profession(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                category_id=category_id,
                category_code=category_code,
                title=title,
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_selected_profession_id=str(item.profession_id),
    )
    await state.set_state(None)

    await message.answer(
        t("admin_dict_profession_create_done", language),
    )
    await message.answer(
        format_admin_profession_card(item, language),
        reply_markup=admin_profession_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_PROF_RENAME")
async def admin_profession_rename_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    if not data.get("admin_selected_profession_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_profession_rename)
    await callback.message.answer(
        t("admin_dict_profession_rename_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_profession_rename)
async def admin_profession_rename_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).rename_profession(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                profession_id=profession_id,
                title=message.text or "",
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_selected_profession_id=str(item.profession_id),
    )
    await state.set_state(None)

    await message.answer(
        t("admin_dict_profession_rename_done", language),
    )
    await message.answer(
        format_admin_profession_card(item, language),
        reply_markup=admin_profession_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_PROF_MOVE")
async def admin_profession_move_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    if not data.get("admin_selected_profession_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_profession_move)
    await callback.message.answer(
        t("admin_dict_profession_move_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_profession_move)
async def admin_profession_move_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).move_profession_to_category(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                profession_id=profession_id,
                category_code=message.text or "",
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_selected_profession_id=str(item.profession_id),
    )
    await state.set_state(None)

    await message.answer(
        t("admin_dict_profession_move_done", language),
    )
    await message.answer(
        format_admin_profession_card(item, language),
        reply_markup=admin_profession_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_PROF_TOGGLE")
async def admin_profession_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).toggle_profession_visibility(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                profession_id=profession_id,
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_profession_id=str(item.profession_id),
    )

    await callback.message.answer(
        t("admin_dict_profession_visibility_done", language),
    )
    await callback.message.answer(
        format_admin_profession_card(item, language),
        reply_markup=admin_profession_card_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_PROF_ARCHIVE")
async def admin_profession_archive(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            service = DictionaryService(DictionaryRepository(session))
            current_item = await service.get_profession_card(
                profession_id=profession_id,
                language=language,
            )

            if not current_item:
                raise DictionaryServiceError("admin_item_not_found")

            if current_item.status_code == "archived":
                item = await service.unarchive_profession(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    profession_id=profession_id,
                    language=language,
                )
                done_text_key = "admin_dict_profession_unarchive_done"
            else:
                item = await service.archive_profession(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    profession_id=profession_id,
                    language=language,
                )
                done_text_key = "admin_dict_profession_archive_done"

            await session.commit()

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_profession_id=str(item.profession_id),
    )

    await callback.message.answer(
        t(done_text_key, language),
    )
    await callback.message.answer(
        format_admin_profession_card(item, language),
        reply_markup=admin_profession_card_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_SPEC_MOVE_ALL")
async def admin_specialist_move_all(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            specialist_ids = await DictionaryService(
                DictionaryRepository(session)
            ).list_profession_specialist_ids(
                profession_id=profession_id,
            )

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    if not specialist_ids:
        await callback.answer(
            t("admin_dict_specialist_move_empty", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_specialist_move_ids=specialist_ids,
        admin_move_source_type="profession",
        admin_move_source_id=profession_id,
        admin_move_specialist_ids=specialist_ids,
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_target_profession_ids=[],
        admin_move_mode=None,
    )
    await show_admin_multi_move_categories(
        callback.message,
        state,
        language,
    )
    await callback.answer()



@admin_router.callback_query(F.data == "ADM_SPEC_MOVE_SELECT")
async def admin_specialist_move_select_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    specialist_ids = data.get("admin_profession_specialist_ids") or []

    if not specialist_ids:
        await callback.answer(
            t("admin_dict_specialist_move_empty", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_specialist_move_numbers)
    await callback.message.answer(
        t("admin_dict_specialist_move_select_prompt", language)
    )
    await callback.answer()

@admin_router.message(AdminModerationFSM.entering_admin_specialist_move_numbers)
async def admin_specialist_move_numbers_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    data = await state.get_data()
    specialist_ids = data.get("admin_profession_specialist_ids") or []

    if not specialist_ids:
        await state.clear()
        await message.answer(t("admin_dict_specialist_move_empty", language))
        return

    raw_numbers = [
        item.strip()
        for item in (message.text or "").replace(";", ",").split(",")
        if item.strip()
    ]

    selected_indexes = []

    try:
        selected_indexes = [
            int(item) - 1
            for item in raw_numbers
        ]
    except ValueError:
        await message.answer(
            t("admin_dict_specialist_move_bad_numbers", language).format(
                count=len(specialist_ids),
            )
        )
        return

    if (
        not selected_indexes
        or any(index < 0 or index >= len(specialist_ids) for index in selected_indexes)
    ):
        await message.answer(
            t("admin_dict_specialist_move_bad_numbers", language).format(
                count=len(specialist_ids),
            )
        )
        return

    selected_specialist_ids = [
        specialist_ids[index]
        for index in dict.fromkeys(selected_indexes)
    ]

    await state.update_data(
        admin_selected_specialist_move_ids=(
            selected_specialist_ids
        ),
        admin_move_source_type="profession",
        admin_move_source_id=data.get(
            "admin_selected_profession_id"
        ),
        admin_move_specialist_ids=(
            selected_specialist_ids
        ),
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_target_profession_ids=[],
        admin_move_mode=None,
    )
    await show_admin_multi_move_categories(
        message,
        state,
        language,
    )

def admin_multi_move_confirm_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_confirm_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="ADM_MULTI_BACK_MODE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_cancel_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_MOVE_CANCEL",
                )
            ],
        ]
    )

def admin_multi_move_mode_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_move_mode_replace_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_MODE:replace",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_move_mode_add_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_MODE:add",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("billing_back", language),
                    callback_data="ADM_MULTI_BACK_PROF",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_cancel_btn",
                        language,
                    ),
                    callback_data="ADM_MULTI_MOVE_CANCEL",
                )
            ],
        ]
    )
def admin_multi_move_profession_keyboard(
    items,
    selected_ids: list[str],
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    selected_set = set(selected_ids)
    rows = []

    for index, item in enumerate(items):
        item_id = str(item.profession_id)
        marker = "✓ " if item_id in selected_set else ""

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{item.title}",
                    callback_data=f"ADM_MULTI_PROF:{index}",
                )
            ]
        )

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_MULTI_PROF_PAGE:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_MULTI_PROF_PAGE:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_move_professions_done_btn",
                    language,
                ),
                callback_data="ADM_MULTI_PROF_DONE",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_move_back_categories_btn",
                    language,
                ),
                callback_data="ADM_MULTI_BACK_CAT",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_specialist_move_cancel_btn",
                    language,
                ),
                callback_data="ADM_MULTI_MOVE_CANCEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_admin_multi_move_professions(
    message: Message,
    state: FSMContext,
    language: str,
    *,
    page: int = 0,
    edit: bool = False,
):
    data = await state.get_data()
    category_id = data.get(
        "admin_move_target_category_id"
    )
    selected_ids = data.get(
        "admin_move_selected_profession_ids"
    ) or []

    if not category_id:
        await message.answer(
            t("admin_item_not_found", language)
        )
        return

    async with get_session() as session:
        service = DictionaryService(
            DictionaryRepository(session)
        )
        category = await service.get_category_card(
            category_id=category_id,
            language=language,
        )
        professions = (
            await service
            .list_active_professions_for_category(
                category_id=category_id,
                language=language,
            )
        )

    if not category:
        await message.answer(
            t("admin_item_not_found", language)
        )
        return

    start = page * ADMIN_MOVE_PROFESSIONS_PAGE_SIZE
    end = start + ADMIN_MOVE_PROFESSIONS_PAGE_SIZE
    visible_professions = professions[start:end]
    has_next = end < len(professions)

    if not visible_professions and page > 0:
        page = 0
        start = 0
        end = ADMIN_MOVE_PROFESSIONS_PAGE_SIZE
        visible_professions = professions[start:end]
        has_next = end < len(professions)

    await state.update_data(
        admin_move_available_profession_ids=[
            str(profession.profession_id)
            for profession in visible_professions
        ],
        admin_move_professions_page=page,
    )
    await state.set_state(
        AdminModerationFSM
        .entering_admin_move_target_professions
    )

    selected_titles = [
        profession.title
        for profession in professions
        if str(profession.profession_id)
        in set(selected_ids)
    ]

    if selected_titles:
        selected_text = t(
            "admin_dict_move_selected_professions",
            language,
        ).format(
            items=", ".join(selected_titles),
        )
    else:
        selected_text = t(
            "admin_dict_move_selected_professions_empty",
            language,
        )

    category_text = t(
        "admin_dict_move_selected_category",
        language,
    ).format(
        category=category.title,
    )

    screen_text = (
        f"{t('admin_dict_move_choose_professions', language)}"
        f"\n\n{category_text}"
        f"\n{selected_text}"
    )
    keyboard = admin_multi_move_profession_keyboard(
        visible_professions,
        selected_ids,
        page=page,
        has_next=has_next,
        language=language,
    )

    if edit:
        await message.edit_text(
            screen_text,
            reply_markup=keyboard,
        )
    else:
        await message.answer(
            screen_text,
            reply_markup=keyboard,
        )

@admin_router.callback_query(
    F.data.startswith("ADM_MULTI_PROF_PAGE:")
)
async def admin_multi_move_profession_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (TypeError, ValueError):
        page = 0

    await show_admin_multi_move_professions(
        callback.message,
        state,
        language,
        page=page,
        edit=True,
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_MULTI_PROF:")
)
async def admin_multi_move_profession_toggle(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    profession_ids = data.get(
        "admin_move_available_profession_ids"
    ) or []
    selected_ids = list(
        data.get(
            "admin_move_selected_profession_ids"
        ) or []
    )

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
        profession_id = profession_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if profession_id in selected_ids:
        selected_ids = [
            item
            for item in selected_ids
            if item != profession_id
        ]
    else:
        if (
            len(selected_ids)
            >= MAX_PROFESSIONS_PER_CATEGORY
        ):
            await callback.answer(
                t(
                    "spec_profession_limit_per_category",
                    language,
                ),
                show_alert=True,
            )
            return

        selected_ids.append(profession_id)

    await state.update_data(
        admin_move_selected_profession_ids=selected_ids
    )

    await show_admin_multi_move_professions(
        callback.message,
        state,
        language,
        page=int(
            data.get("admin_move_professions_page") or 0
        ),
        edit=True,
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_MULTI_PROF_DONE"
)
async def admin_multi_move_professions_done(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    selected_ids = data.get(
        "admin_move_selected_profession_ids"
    ) or []

    if not selected_ids:
        await callback.answer(
            t("spec_profession_select_one", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.choosing_admin_move_mode
    )
    await callback.message.answer(
        t("admin_dict_move_mode_prompt", language),
        reply_markup=admin_multi_move_mode_keyboard(
            language
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_MULTI_BACK_CAT"
)
async def admin_multi_move_back_categories(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await show_admin_multi_move_categories(
        callback.message,
        state,
        language,
        edit=True,
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_MULTI_BACK_MODE"
)
async def admin_multi_move_back_mode(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.set_state(
        AdminModerationFSM.choosing_admin_move_mode
    )
    await callback.message.answer(
        t("admin_dict_move_mode_prompt", language),
        reply_markup=admin_multi_move_mode_keyboard(
            language
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_MULTI_CONFIRM"
)
async def admin_multi_move_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await DictionaryService(
                DictionaryRepository(session)
            ).move_specialists_to_multiple_professions(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                source_type=data.get("admin_move_source_type"),
                source_id=data.get("admin_move_source_id"),
                target_category_id=data.get(
                    "admin_move_target_category_id"
                ),
                target_profession_ids=data.get(
                    "admin_move_selected_profession_ids"
                ) or [],
                specialist_ids=data.get(
                    "admin_move_specialist_ids"
                ) or [],
                mode=data.get("admin_move_mode"),
                language=language,
            )

            await session.commit()

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    mode_key = (
        "admin_dict_move_mode_replace_label"
        if result.mode == "replace"
        else "admin_dict_move_mode_add_label"
    )

    await state.clear()

    await callback.message.answer(
        t(
            "admin_dict_multi_move_done",
            language,
        ).format(
            target_category=result.target_category.title,
            target_professions=", ".join(
                profession.title
                for profession
                in result.target_professions
            ),
            mode=t(mode_key, language),
            specialists_count=(
                result.requested_specialists_count
            ),
            created_count=result.created_links_count,
            reactivated_count=(
                result.reactivated_links_count
            ),
            existing_count=result.existing_links_count,
            deleted_count=(
                result.deleted_old_links_count
            ),
            synchronized_count=(
                result.synchronized_primary_count
            ),
            missing_count=(
                result.missing_specialists_count
            ),
        ),
        reply_markup=admin_dictionaries_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_MULTI_MODE:")
)
async def admin_multi_move_mode_selected(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    mode = (callback.data or "").split(":", 1)[1]

    if mode not in {"replace", "add"}:
        await callback.answer(
            t("admin_dict_move_mode_invalid", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    source_type = data.get("admin_move_source_type")
    source_id = data.get("admin_move_source_id")
    category_id = data.get(
        "admin_move_target_category_id"
    )
    profession_ids = data.get(
        "admin_move_selected_profession_ids"
    ) or []
    specialist_ids = data.get(
        "admin_move_specialist_ids"
    ) or []

    try:
        async with get_session() as session:
            preview = await DictionaryService(
                DictionaryRepository(session)
            ).preview_multi_profession_move(
                source_type=source_type,
                source_id=source_id,
                target_category_id=category_id,
                target_profession_ids=profession_ids,
                specialist_ids=specialist_ids,
                mode=mode,
                language=language,
            )
    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_move_mode=mode
    )
    await state.set_state(
        AdminModerationFSM.confirming_admin_multi_move
    )

    mode_key = (
        "admin_dict_move_mode_replace_label"
        if mode == "replace"
        else "admin_dict_move_mode_add_label"
    )

    await callback.message.answer(
        t(
            "admin_dict_multi_move_preview",
            language,
        ).format(
            source=preview.source_title,
            target_category=(
                preview.target_category.title
            ),
            target_professions=", ".join(
                profession.title
                for profession
                in preview.target_professions
            ),
            mode=t(mode_key, language),
            specialists_count=len(
                preview.selected_specialists
            ),
        ),
        reply_markup=admin_multi_move_confirm_keyboard(
            language
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_MULTI_BACK_PROF"
)
async def admin_multi_move_back_professions(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await show_admin_multi_move_professions(
        callback.message,
        state,
        language,
    )
    await callback.answer()

def admin_multi_move_category_keyboard(
    items,
    *,
    selected_category_id: str | None,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, item in enumerate(items):
        item_id = str(item.category_id)
        marker = (
            "✓ "
            if item_id == selected_category_id
            else ""
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{item.title}",
                    callback_data=f"ADM_MULTI_CAT:{index}",
                )
            ]
        )

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_MULTI_CAT_PAGE:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_MULTI_CAT_PAGE:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_specialist_move_cancel_btn",
                    language,
                ),
                callback_data="ADM_MULTI_MOVE_CANCEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_admin_multi_move_categories(
    message: Message,
    state: FSMContext,
    language: str,
    *,
    page: int = 0,
    edit: bool = False,
):
    data = await state.get_data()
    selected_category_id = data.get(
        "admin_move_target_category_id"
    )

    async with get_session() as session:
        categories = await DictionaryService(
            DictionaryRepository(session)
        ).list_specialist_move_target_categories(
            language=language,
        )

    start = page * ADMIN_MOVE_CATEGORIES_PAGE_SIZE
    end = start + ADMIN_MOVE_CATEGORIES_PAGE_SIZE
    visible_categories = categories[start:end]
    has_next = end < len(categories)

    if not visible_categories and page > 0:
        page = 0
        start = 0
        end = ADMIN_MOVE_CATEGORIES_PAGE_SIZE
        visible_categories = categories[start:end]
        has_next = end < len(categories)

    await state.update_data(
        admin_move_available_category_ids=[
            str(category.category_id)
            for category in visible_categories
        ],
        admin_move_categories_page=page,
    )
    await state.set_state(
        AdminModerationFSM.choosing_admin_move_mode
    )

    keyboard = admin_multi_move_category_keyboard(
        visible_categories,
        selected_category_id=selected_category_id,
        page=page,
        has_next=has_next,
        language=language,
    )
    screen_text = t(
        "admin_dict_move_choose_category",
        language,
    )

    if edit:
        await message.edit_text(
            screen_text,
            reply_markup=keyboard,
        )
    else:
        await message.answer(
            screen_text,
            reply_markup=keyboard,
        )

@admin_router.callback_query(
    F.data.startswith("ADM_MULTI_CAT_PAGE:")
)
async def admin_multi_move_category_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        page = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (TypeError, ValueError):
        page = 0

    await show_admin_multi_move_categories(
        callback.message,
        state,
        language,
        page=page,
        edit=True,
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_MULTI_CAT:")
)
async def admin_multi_move_category_selected(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    previous_category_id = data.get(
        "admin_move_target_category_id"
    )
    previous_selected_ids = data.get(
        "admin_move_selected_profession_ids"
    ) or []
    category_ids = data.get(
        "admin_move_available_category_ids"
    ) or []

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
        category_id = category_ids[index]
    except (IndexError, TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            professions = await DictionaryService(
                DictionaryRepository(session)
            ).list_active_professions_for_category(
                category_id=category_id,
                language=language,
            )
    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    selected_profession_ids = (
        previous_selected_ids
        if previous_category_id == category_id
        else []
    )

    await state.update_data(
        admin_move_target_category_id=category_id,
        admin_move_available_profession_ids=[
            str(profession.profession_id)
            for profession in professions
        ],
        admin_move_selected_profession_ids=(
            selected_profession_ids
        ),
    )
    await show_admin_multi_move_professions(
        callback.message,
        state,
        language,
        page=0,
        edit=True,
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_MULTI_MOVE_CANCEL"
)
async def admin_multi_move_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.clear()
    await callback.message.answer(
        t("admin_cancelled", language),
        reply_markup=admin_dictionaries_keyboard(language),
    )
    await callback.answer()

def admin_specialist_move_confirm_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_confirm_btn",
                        language,
                    ),
                    callback_data="ADM_SPEC_MOVE_CONFIRM",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_cancel_btn",
                        language,
                    ),
                    callback_data="ADM_SPEC_MOVE_CANCEL",
                )
            ],
        ]
    )


@admin_router.message(
    AdminModerationFSM.entering_admin_specialist_move_target
)
async def admin_specialist_move_target_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    data = await state.get_data()

    source_profession_id = data.get(
        "admin_selected_profession_id"
    )
    specialist_ids = data.get(
        "admin_selected_specialist_move_ids"
    ) or []
    candidate_ids = data.get(
        "admin_specialist_move_target_candidate_ids"
    ) or []

    if not source_profession_id or not specialist_ids:
        await state.clear()
        await message.answer(
            t("admin_dict_specialist_move_empty", language)
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.clear()
        await message.answer(
            t("admin_access_denied", language)
        )
        return

    entered_value = " ".join((message.text or "").split())
    target_profession_id = None

    try:
        async with get_session() as session:
            service = DictionaryService(
                DictionaryRepository(session)
            )

            if candidate_ids and entered_value.isdigit():
                selected_index = int(entered_value) - 1

                if (
                    selected_index < 0
                    or selected_index >= len(candidate_ids)
                ):
                    await message.answer(
                        t(
                            "admin_dict_specialist_move_target_bad_number",
                            language,
                        ).format(
                            count=len(candidate_ids),
                        )
                    )
                    return

                target_profession_id = candidate_ids[
                    selected_index
                ]

            else:
                targets = await service.find_specialist_move_targets(
                    title=entered_value,
                    source_profession_id=source_profession_id,
                    language=language,
                )

                if len(targets) > 1:
                    await state.update_data(
                        admin_specialist_move_target_candidate_ids=[
                            str(target.profession_id)
                            for target in targets
                        ]
                    )

                    choices = [
                        t(
                            "admin_dict_specialist_move_target_multiple",
                            language,
                        ),
                        "",
                    ]

                    for index, target in enumerate(
                        targets,
                        start=1,
                    ):
                        choices.append(
                            f"{index}. {target.title} | "
                            f"{target.category_name}"
                        )

                    await message.answer("\n".join(choices))
                    return

                target_profession_id = str(
                    targets[0].profession_id
                )

            preview = await service.preview_specialist_move(
                source_profession_id=source_profession_id,
                target_profession_id=target_profession_id,
                specialist_ids=specialist_ids,
                language=language,
            )

    except DictionaryServiceError as exc:
        await message.answer(
            t(exc.text_key, language)
        )
        return

    await state.update_data(
        admin_specialist_move_target_id=target_profession_id,
        admin_specialist_move_target_candidate_ids=[],
    )
    await state.set_state(
        AdminModerationFSM.confirming_admin_specialist_move
    )

    await message.answer(
        t(
            "admin_dict_specialist_move_preview",
            language,
        ).format(
            source_profession=preview.source_profession.title,
            source_category=preview.source_profession.category_name,
            target_profession=preview.target_profession.title,
            target_category=preview.target_profession.category_name,
            count=len(preview.selected_specialists),
        ),
        reply_markup=admin_specialist_move_confirm_keyboard(
            language
        ),
    )


@admin_router.callback_query(
    F.data == "ADM_SPEC_MOVE_CANCEL"
)
async def admin_specialist_move_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.update_data(
        admin_specialist_move_target_id=None,
        admin_specialist_move_target_candidate_ids=[],
    )
    await state.set_state(
        AdminModerationFSM.entering_admin_specialist_move_target
    )

    await callback.message.answer(
        t("admin_dict_specialist_move_target_prompt", language)
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_SPEC_MOVE_CONFIRM"
)
async def admin_specialist_move_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    source_profession_id = data.get(
        "admin_selected_profession_id"
    )
    target_profession_id = data.get(
        "admin_specialist_move_target_id"
    )
    specialist_ids = data.get(
        "admin_selected_specialist_move_ids"
    ) or []

    if (
        not source_profession_id
        or not target_profession_id
        or not specialist_ids
    ):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await DictionaryService(
                DictionaryRepository(session)
            ).move_specialists_to_profession(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                source_profession_id=source_profession_id,
                target_profession_id=target_profession_id,
                specialist_ids=specialist_ids,
                language=language,
            )

            await session.commit()

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.clear()

    await callback.message.answer(
        t(
            "admin_dict_specialist_move_done",
            language,
        ).format(
            target_profession=result.target_profession.title,
            target_category=result.target_profession.category_name,
            moved_count=result.moved_count,
            duplicate_count=result.archived_duplicate_count,
            synchronized_count=(
                result.synchronized_primary_count
            ),
            missing_count=result.missing_count,
        ),
        reply_markup=admin_dictionaries_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_PROF_SPECIALISTS")
@admin_router.callback_query(F.data.startswith("ADM_PROF_SPECIALISTS:"))
async def admin_profession_specialists(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    data = await state.get_data()
    profession_id = data.get("admin_selected_profession_id")

    if not profession_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await DictionaryService(
                DictionaryRepository(session)
            ).list_profession_specialists(
                profession_id=profession_id,
                limit=ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE + 1,
                offset=page * ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE,
            )

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    has_next = len(items) > ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE
    visible_items = items[:ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE]

    await state.update_data(
        admin_profession_specialist_ids=[
            str(item.specialist_id)
            for item in visible_items
        ],
        admin_profession_specialists_page=page,
    )

    await callback.message.answer(
        format_admin_profession_specialists_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_profession_specialists_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_DICT_GEO")
@admin_router.callback_query(F.data.startswith("ADM_DICT_GEO:"))
async def admin_geo_dictionary(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        items = await DictionaryService(
            DictionaryRepository(session)
        ).list_country_cards(
            limit=ADMIN_COUNTRIES_PAGE_SIZE + 1,
            offset=page * ADMIN_COUNTRIES_PAGE_SIZE,
            language=language,
        )

    has_next = len(items) > ADMIN_COUNTRIES_PAGE_SIZE
    visible_items = items[:ADMIN_COUNTRIES_PAGE_SIZE]

    await state.update_data(
        admin_country_ids=[
            str(item.country_id)
            for item in visible_items
        ],
        admin_country_page=page,
    )

    await callback.message.answer(
        format_admin_countries_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_countries_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_COUNTRY_CREATE")
async def admin_country_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_country_create)
    await callback.message.answer(t("admin_dict_country_create_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_country_create)
async def admin_country_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).create_country(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                payload=message.text or "",
                language=language,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(admin_selected_country_id=str(item.country_id))
    await state.set_state(AdminModerationFSM.entering_admin_country_number)

    await message.answer(t("admin_dict_country_create_done", language))
    await message.answer(
        format_admin_country_card(item, language),
        reply_markup=admin_country_card_keyboard(language),
    )


@admin_router.callback_query(F.data == "ADM_COUNTRY_IMPORT")
async def admin_country_import_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_country_import)
    await callback.message.answer(t("admin_dict_country_import_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_country_import)
async def admin_country_import_receive(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    language = normalize_language(message.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    payload = await read_admin_csv_payload_from_message(
        message,
        bot,
        language,
    )

    if payload is None:
        return

    try:
        async with get_session() as session:
            result = await DictionaryService(
                DictionaryRepository(session)
            ).import_countries(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                payload=payload,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.set_state(AdminModerationFSM.entering_admin_country_number)
    await message.answer(
        format_admin_dictionary_import_result(result, language)
    )


@admin_router.callback_query(F.data == "ADM_COUNTRY_OPEN")
async def admin_country_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    country_ids = data.get("admin_country_ids") or []

    if not country_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_country_number)

    await callback.message.answer(
        t("admin_dict_country_open_prompt", language).format(
            count=len(country_ids)
        )
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_country_number)
async def admin_country_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    data = await state.get_data()
    country_ids = data.get("admin_country_ids") or []

    try:
        selected_index = int((message.text or "").strip()) - 1
    except ValueError:
        await message.answer(
            t("admin_dict_country_open_bad_number", language).format(
                count=len(country_ids)
            )
        )
        return

    if selected_index < 0 or selected_index >= len(country_ids):
        await message.answer(
            t("admin_dict_country_open_bad_number", language).format(
                count=len(country_ids)
            )
        )
        return

    selected_country_id = country_ids[selected_index]

    async with get_session() as session:
        item = await DictionaryService(
            DictionaryRepository(session)
        ).get_country_card(
            selected_country_id,
            language=language,
        )

    if not item:
        await message.answer(t("admin_item_not_found", language))
        return

    await state.update_data(admin_selected_country_id=str(item.country_id))
    await state.set_state(AdminModerationFSM.entering_admin_country_number)

    await message.answer(
        format_admin_country_card(item, language),
        reply_markup=admin_country_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_COUNTRY_UPDATE")
async def admin_country_update_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_country_update)
    await callback.message.answer(t("admin_dict_country_update_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_country_update)
async def admin_country_update_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).update_country(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                country_id=country_id,
                payload=message.text or "",
                language=language,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(admin_selected_country_id=str(item.country_id))
    await state.set_state(AdminModerationFSM.entering_admin_country_number)

    await message.answer(t("admin_dict_country_update_done", language))
    await message.answer(
        format_admin_country_card(item, language),
        reply_markup=admin_country_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_COUNTRY_TOGGLE")
async def admin_country_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).toggle_country_visibility(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                country_id=country_id,
                language=language,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await callback.message.answer(t(exc.text_key, language))
        await callback.answer()
        return

    await state.update_data(admin_selected_country_id=str(item.country_id))
    await state.set_state(AdminModerationFSM.entering_admin_country_number)

    await callback.message.answer(t("admin_dict_country_visibility_done", language))
    await callback.message.answer(
        format_admin_country_card(item, language),
        reply_markup=admin_country_card_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_CITY_CREATE")
async def admin_city_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_city_create)
    await callback.message.answer(t("admin_dict_city_create_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_city_create)
async def admin_city_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).create_city(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                country_id=country_id,
                payload=message.text or "",
                language=language,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(admin_selected_city_id=str(item.city_id))
    await state.set_state(AdminModerationFSM.entering_admin_city_number)

    await message.answer(t("admin_dict_city_create_done", language))
    await message.answer(
        format_admin_city_card(item, language),
        reply_markup=admin_city_card_keyboard(language),
    )


@admin_router.callback_query(F.data == "ADM_CITY_IMPORT")
async def admin_city_import_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_city_import)
    await callback.message.answer(t("admin_dict_city_import_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_city_import)
async def admin_city_import_receive(
    message: Message,
    state: FSMContext,
    bot: Bot,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    payload = await read_admin_csv_payload_from_message(
        message,
        bot,
        language,
    )

    if payload is None:
        return

    try:
        async with get_session() as session:
            result = await DictionaryService(
                DictionaryRepository(session)
            ).import_cities(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                country_id=country_id,
                payload=payload,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(admin_selected_country_id=country_id)
    await state.set_state(AdminModerationFSM.entering_admin_city_number)
    await message.answer(
        format_admin_dictionary_import_result(result, language)
    )


@admin_router.callback_query(F.data == "ADM_COUNTRY_CITIES")
@admin_router.callback_query(F.data.startswith("ADM_COUNTRY_CITIES:"))
async def admin_country_cities(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    country_id = data.get("admin_selected_country_id")

    if not country_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    async with get_session() as session:
        items = await DictionaryService(
            DictionaryRepository(session)
        ).list_city_cards(
            country_id=country_id,
            limit=ADMIN_CITIES_PAGE_SIZE + 1,
            offset=page * ADMIN_CITIES_PAGE_SIZE,
            language=language,
        )

    has_next = len(items) > ADMIN_CITIES_PAGE_SIZE
    visible_items = items[:ADMIN_CITIES_PAGE_SIZE]

    await state.update_data(
        admin_city_ids=[
            str(item.city_id)
            for item in visible_items
        ],
        admin_city_page=page,
    )

    await callback.message.answer(
        format_admin_cities_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_cities_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_CITY_UPDATE")
async def admin_city_update_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    city_id = data.get("admin_selected_city_id")

    if not city_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_city_update)
    await callback.message.answer(t("admin_dict_city_update_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_city_update)
async def admin_city_update_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    city_id = data.get("admin_selected_city_id")

    if not city_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).update_city(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                city_id=city_id,
                payload=message.text or "",
                language=language,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(admin_selected_city_id=str(item.city_id))
    await state.set_state(AdminModerationFSM.entering_admin_city_number)

    await message.answer(t("admin_dict_city_update_done", language))
    await message.answer(
        format_admin_city_card(item, language),
        reply_markup=admin_city_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_CITY_GEO_UPDATE")
async def admin_city_geo_update_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    city_id = data.get("admin_selected_city_id")

    if not city_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_city_geo_update)
    await callback.message.answer(t("admin_dict_city_geo_update_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_city_geo_update)
async def admin_city_geo_update_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    city_id = data.get("admin_selected_city_id")

    if not city_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).update_city_geo(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                city_id=city_id,
                payload=message.text or "",
                language=language,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(admin_selected_city_id=str(item.city_id))
    await state.set_state(AdminModerationFSM.entering_admin_city_number)

    await message.answer(t("admin_dict_city_geo_update_done", language))
    await message.answer(
        format_admin_city_card(item, language),
        reply_markup=admin_city_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_CITY_TOGGLE")
async def admin_city_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    city_id = data.get("admin_selected_city_id")

    if not city_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).toggle_city_visibility(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                city_id=city_id,
                language=language,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await callback.message.answer(t(exc.text_key, language))
        await callback.answer()
        return

    await state.update_data(admin_selected_city_id=str(item.city_id))
    await state.set_state(AdminModerationFSM.entering_admin_city_number)

    await callback.message.answer(t("admin_dict_city_visibility_done", language))
    await callback.message.answer(
        format_admin_city_card(item, language),
        reply_markup=admin_city_card_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_CITY_OPEN")
async def admin_city_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    city_ids = data.get("admin_city_ids") or []

    if not city_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_city_number)

    await callback.message.answer(
        t("admin_dict_city_open_prompt", language).format(
            count=len(city_ids)
        )
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_city_number)
async def admin_city_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    data = await state.get_data()
    city_ids = data.get("admin_city_ids") or []

    try:
        selected_index = int((message.text or "").strip()) - 1
    except ValueError:
        await message.answer(
            t("admin_dict_city_open_bad_number", language).format(
                count=len(city_ids)
            )
        )
        return

    if selected_index < 0 or selected_index >= len(city_ids):
        await message.answer(
            t("admin_dict_city_open_bad_number", language).format(
                count=len(city_ids)
            )
        )
        return

    selected_city_id = city_ids[selected_index]

    async with get_session() as session:
        item = await DictionaryService(
            DictionaryRepository(session)
        ).get_city_card(
            selected_city_id,
            language=language,
        )

    if not item:
        await message.answer(t("admin_item_not_found", language))
        return

    await state.update_data(admin_selected_city_id=str(item.city_id))
    await state.set_state(AdminModerationFSM.entering_admin_city_number)

    await message.answer(
        format_admin_city_card(item, language),
        reply_markup=admin_city_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_DICT_LANGUAGES")
@admin_router.callback_query(F.data.startswith("ADM_DICT_LANGUAGES:"))
async def admin_languages_dictionary(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        items = await DictionaryService(
            DictionaryRepository(session)
        ).list_language_cards(
            limit=ADMIN_LANGUAGES_PAGE_SIZE + 1,
            offset=page * ADMIN_LANGUAGES_PAGE_SIZE,
        )

    has_next = len(items) > ADMIN_LANGUAGES_PAGE_SIZE
    visible_items = items[:ADMIN_LANGUAGES_PAGE_SIZE]

    await state.update_data(
        admin_language_codes=[
            item.code
            for item in visible_items
        ],
        admin_language_page=page,
    )

    await callback.message.answer(
        format_admin_languages_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_languages_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_LANGUAGE_CREATE")
async def admin_language_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_language_create)
    await callback.message.answer(
        t("admin_dict_language_create_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_language_create)
async def admin_language_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).create_language(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                payload=message.text or "",
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(admin_selected_language_code=item.code)
    await state.set_state(AdminModerationFSM.entering_admin_language_number)

    await message.answer(t("admin_dict_language_create_done", language))
    await message.answer(
        format_admin_language_card(item, language),
        reply_markup=admin_language_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_LANGUAGE_TOGGLE")
async def admin_language_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    language_code = data.get("admin_selected_language_code")

    if not language_code:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).toggle_language_visibility(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                code=language_code,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await callback.message.answer(t(exc.text_key, language))
        await callback.answer()
        return

    await state.update_data(admin_selected_language_code=item.code)
    await state.set_state(AdminModerationFSM.entering_admin_language_number)

    await callback.message.answer(t("admin_dict_language_visibility_done", language))
    await callback.message.answer(
        format_admin_language_card(item, language),
        reply_markup=admin_language_card_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_LANGUAGE_RENAME")
async def admin_language_rename_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    language_code = data.get("admin_selected_language_code")

    if not language_code:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_language_rename)
    await callback.message.answer(
        t("admin_dict_language_rename_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_language_rename)
async def admin_language_rename_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    language_code = data.get("admin_selected_language_code")

    if not language_code:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).rename_language(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                code=language_code,
                payload=message.text or "",
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(admin_selected_language_code=item.code)
    await state.set_state(AdminModerationFSM.entering_admin_language_number)

    await message.answer(t("admin_dict_language_rename_done", language))
    await message.answer(
        format_admin_language_card(item, language),
        reply_markup=admin_language_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_LANGUAGE_OPEN")
async def admin_language_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    language_codes = data.get("admin_language_codes") or []

    if not language_codes:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_language_number)

    await callback.message.answer(
        t("admin_dict_language_open_prompt", language).format(
            count=len(language_codes)
        )
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_language_number)
async def admin_language_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    data = await state.get_data()
    language_codes = data.get("admin_language_codes") or []

    try:
        selected_index = int((message.text or "").strip()) - 1
    except ValueError:
        await message.answer(
            t("admin_dict_language_open_bad_number", language).format(
                count=len(language_codes)
            )
        )
        return

    if selected_index < 0 or selected_index >= len(language_codes):
        await message.answer(
            t("admin_dict_language_open_bad_number", language).format(
                count=len(language_codes)
            )
        )
        return

    selected_code = language_codes[selected_index]

    async with get_session() as session:
        item = await DictionaryService(
            DictionaryRepository(session)
        ).get_language_card(selected_code)

    if not item:
        await message.answer(t("admin_item_not_found", language))
        return

    await state.update_data(admin_selected_language_code=item.code)
    await state.set_state(AdminModerationFSM.entering_admin_language_number)

    await message.answer(
        format_admin_language_card(item, language),
        reply_markup=admin_language_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_DICT_SKILLS")
@admin_router.callback_query(F.data.startswith("ADM_DICT_SKILLS:"))
async def admin_skills_dictionary(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    async with get_session() as session:
        items = await DictionaryService(
            DictionaryRepository(session)
        ).list_skill_cards(
            language=language,
            limit=ADMIN_SKILLS_PAGE_SIZE + 1,
            offset=page * ADMIN_SKILLS_PAGE_SIZE,
        )

    has_next = len(items) > ADMIN_SKILLS_PAGE_SIZE
    visible_items = items[:ADMIN_SKILLS_PAGE_SIZE]

    await state.update_data(
        admin_skill_ids=[
            str(item.skill_id)
            for item in visible_items
        ],
        admin_skill_page=page,
    )

    await callback.message.answer(
        format_admin_skills_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_skills_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "ADM_SKILL_OPEN")
async def admin_skill_open_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    skill_ids = data.get("admin_skill_ids") or []

    if not skill_ids:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_skill_number)

    await callback.message.answer(
        t("admin_dict_skill_open_prompt", language).format(
            count=len(skill_ids),
        )
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_skill_number)
async def admin_skill_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    skill_ids = data.get("admin_skill_ids") or []

    try:
        index = int((message.text or "").strip()) - 1
    except ValueError:
        await message.answer(
            t("admin_dict_skill_open_bad_number", language).format(
                count=len(skill_ids),
            )
        )
        return

    if index < 0 or index >= len(skill_ids):
        await message.answer(
            t("admin_dict_skill_open_bad_number", language).format(
                count=len(skill_ids),
            )
        )
        return

    skill_id = skill_ids[index]

    async with get_session() as session:
        item = await DictionaryService(
            DictionaryRepository(session)
        ).get_skill_card(
            skill_id=skill_id,
            language=language,
        )

    if not item:
        await message.answer(t("admin_item_not_found", language))
        await state.clear()
        return

    await state.update_data(
        admin_selected_skill_id=skill_id,
    )
    await state.set_state(None)

    await message.answer(
        format_admin_skill_card(item, language),
        reply_markup=admin_skill_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_SKILL_CREATE")
async def admin_skill_create_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_skill_create)
    await callback.message.answer(
        t("admin_dict_skill_create_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_skill_create)
async def admin_skill_create_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).create_skill(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                title=message.text or "",
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_selected_skill_id=str(item.skill_id),
    )
    await state.set_state(None)

    await message.answer(
        t("admin_dict_skill_create_done", language),
    )
    await message.answer(
        format_admin_skill_card(item, language),
        reply_markup=admin_skill_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_SKILL_RENAME")
async def admin_skill_rename_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    if not data.get("admin_selected_skill_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_skill_rename)
    await callback.message.answer(
        t("admin_dict_skill_rename_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_skill_rename)
async def admin_skill_rename_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    skill_id = data.get("admin_selected_skill_id")

    if not skill_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).rename_skill(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                skill_id=skill_id,
                title=message.text or "",
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_selected_skill_id=str(item.skill_id),
    )
    await state.set_state(None)

    await message.answer(
        t("admin_dict_skill_rename_done", language),
    )
    await message.answer(
        format_admin_skill_card(item, language),
        reply_markup=admin_skill_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_SKILL_TOGGLE")
async def admin_skill_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    skill_id = data.get("admin_selected_skill_id")

    if not skill_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).toggle_skill_visibility(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                skill_id=skill_id,
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_skill_id=str(item.skill_id),
    )

    await callback.message.answer(
        t("admin_dict_skill_visibility_done", language),
    )
    await callback.message.answer(
        format_admin_skill_card(item, language),
        reply_markup=admin_skill_card_keyboard(language),
    )
    await callback.answer()


@admin_router.callback_query(F.data == "ADM_SKILL_MERGE")
async def admin_skill_merge_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    skill_id = data.get("admin_selected_skill_id")

    if not skill_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_skill_merge)
    await callback.message.answer(
        t("admin_dict_skill_merge_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_skill_merge)
async def admin_skill_merge_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    skill_id = data.get("admin_selected_skill_id")

    if not skill_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            preview = await DictionaryService(
                DictionaryRepository(session)
            ).preview_skill_merge(
                source_skill_id=skill_id,
                target_skill_value=message.text or "",
                language=language,
            )
    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_skill_merge_target_value=message.text or "",
    )
    await state.set_state(AdminModerationFSM.confirming_admin_skill_merge)

    await message.answer(
        t("admin_dict_skill_merge_confirm_text", language).format(
            source_title=preview.source_skill.title,
            source_code=preview.source_skill.code,
            target_title=preview.target_skill.title,
            target_code=preview.target_skill.code,
            source_profession_links=(
                preview.source_skill.profession_links_count
            ),
            source_user_links=preview.source_skill.user_links_count,
        ),
        reply_markup=admin_skill_merge_confirm_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_SKILL_MERGE_CANCEL")
async def admin_skill_merge_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    await state.set_state(AdminModerationFSM.entering_admin_skill_number)
    await state.update_data(admin_skill_merge_target_value=None)

    await callback.message.answer(t("admin_cancelled", language))
    await callback.answer()


@admin_router.callback_query(F.data == "ADM_SKILL_MERGE_CONFIRM")
async def admin_skill_merge_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    skill_id = data.get("admin_selected_skill_id")
    target_value = data.get("admin_skill_merge_target_value")

    if not skill_id or not target_value:
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await DictionaryService(
                DictionaryRepository(session)
            ).merge_skills(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                source_skill_id=skill_id,
                target_skill_value=target_value,
                language=language,
            )
            await session.commit()
    except DictionaryServiceError as exc:
        await callback.message.answer(t(exc.text_key, language))
        await callback.answer()
        return

    await state.update_data(
        admin_selected_skill_id=str(result.target_skill.skill_id),
        admin_skill_merge_target_value=None,
    )
    await state.set_state(AdminModerationFSM.entering_admin_skill_number)

    await callback.message.answer(
        t("admin_dict_skill_merge_done", language).format(
            moved_profession_links=result.moved_profession_links,
            removed_duplicate_profession_links=(
                result.removed_duplicate_profession_links
            ),
            moved_user_links=result.moved_user_links,
            removed_duplicate_user_links=(
                result.removed_duplicate_user_links
            ),
        )
    )
    await callback.message.answer(
        format_admin_skill_card(result.target_skill, language),
        reply_markup=admin_skill_card_keyboard(language),
    )
    await callback.answer()

def admin_dialog_detection_label(
    detected_type: str,
    language: str,
) -> str:
    key_by_type = {
        "phone": "admin_dialog_detection_phone",
        "email": "admin_dialog_detection_email",
        "telegram_username": (
            "admin_dialog_detection_telegram_username"
        ),
        "messenger_phone": (
            "admin_dialog_detection_messenger_phone"
        ),
        "external_payment": (
            "admin_dialog_detection_external_payment"
        ),
    }

    return t(
        key_by_type.get(
            detected_type,
            "admin_dialog_detection_unknown",
        ),
        language,
    )


def admin_dialog_risk_label(
    severity: str | None,
    language: str,
) -> str:
    key_by_severity = {
        "low": "admin_dialog_risk_low",
        "medium": "admin_dialog_risk_medium",
        "high": "admin_dialog_risk_high",
        "critical": "admin_dialog_risk_critical",
    }

    key = key_by_severity.get(
        (severity or "").strip().lower()
    )

    return t(key, language) if key else "—"

def admin_dialog_status_label(
    status: str | None,
    language: str,
) -> str:
    key_by_status = {
        "waiting_specialist": "admin_dialog_status_waiting_specialist",
        "waiting_client": "admin_dialog_status_waiting_client",
        "open": "admin_dialog_status_open",
        "in_discussion": "admin_dialog_status_in_discussion",
        "completed": "admin_dialog_status_completed",
        "closed": "admin_dialog_status_closed",
    }

    key = key_by_status.get(
        (status or "").strip().lower(),
        "admin_dialog_status_other",
    )
    return t(key, language)



def admin_dialog_context_label(
    item,
    language: str,
) -> str:
    parts = []

    if item.has_complaint:
        parts.append(
            t(
                "admin_dialog_context_complaint",
                language,
            )
        )

    if item.has_risk_flag:
        parts.append(
            t(
                "admin_dialog_context_risk",
                language,
            )
        )

    return " + ".join(parts) or "—"

def admin_dialog_queue_keyboard(
    items,
    language: str,
) -> InlineKeyboardMarkup:
    rows = []

    for index, item in enumerate(items):
        rows.append(
            [
                InlineKeyboardButton(
                text=t(
                    "admin_dialog_queue_button",
                    language,
                ).format(
                    number=index + 1,
                    context=admin_dialog_context_label(
                        item,
                        language,
                    ),
                    status=admin_dialog_status_label(
                        item.thread_status,
                        language,
                    ),
                    messages_count=item.messages_count,
                ),
                    callback_data=f"ADM_ADMIN_THREAD:{index}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dialog_back_btn",
                    language,
                ),
                callback_data="ADM_PANEL",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_admin_dialog_contexts(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    (
        admin_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_DIALOGS_MENU_ROLES
        )
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await ModerationService(
                ModerationRepository(session)
            ).open_admin_thread_contexts(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_dialog_thread_ids=[
            str(item.thread_id)
            for item in items
        ],
        admin_dialog_contexts=[
            {
                "has_complaint": item.has_complaint,
                "has_risk_flag": item.has_risk_flag,
                "thread_status": item.thread_status,
            }
            for item in items
        ],
    )

    if not items:
        await callback.message.answer(
            t(
                "admin_dialog_queue_empty",
                language,
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t(
                                "admin_dialog_back_btn",
                                language,
                            ),
                            callback_data="ADM_PANEL",
                        )
                    ]
                ]
            ),
        )
        await callback.answer()
        return

    await callback.message.answer(
        t(
            "admin_dialog_queue_title",
            language,
        ),
        reply_markup=admin_dialog_queue_keyboard(
            items,
            language,
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_DIALOGS_STUB"
)
async def admin_dialogs_entry(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_admin_dialog_contexts(
        callback,
        state,
    )


@admin_router.callback_query(
    F.data.startswith("ADM_ADMIN_THREAD:")
)
async def open_admin_dialog_thread(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(
            (callback.data or "").split(
                ":",
                1,
            )[1]
        )
    except (
        IndexError,
        ValueError,
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    data = await state.get_data()
    thread_ids = data.get(
        "admin_dialog_thread_ids"
    ) or []
    contexts = data.get(
        "admin_dialog_contexts"
    ) or []

    if index < 0 or index >= len(thread_ids):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    (
        admin_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_DIALOGS_MENU_ROLES
        )
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            messages = await ModerationService(
                ModerationRepository(session)
            ).open_admin_thread_messages(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                thread_id=UUID(
                    thread_ids[index]
                ),
            )
    except (
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    if not messages:
        history = t(
            "admin_support_no_messages",
            language,
        )
        thread_status = "—"
    else:
        thread_status = admin_dialog_status_label(
            messages[0].thread_status,
            language,
        )
        history_lines = []

        for message in messages:
            if message.is_masked:
                detected_labels = [
                    admin_dialog_detection_label(
                        detected_type,
                        language,
                    )
                    for detected_type in (
                        message.risk_detected_types
                    )
                ]

                reasons = ", ".join(
                    detected_labels
                ) or t(
                    "admin_dialog_detection_unknown",
                    language,
                )

                message_text = t(
                    "admin_dialog_masked_message",
                    language,
                ).format(
                    reasons=reasons,
                    severity=admin_dialog_risk_label(
                        message.risk_severity,
                        language,
                    ),
                )
            else:
                message_text = (
                    message.original_text
                    or t(
                        "admin_dialog_empty_message",
                        language,
                    )
                )

            sender_label = t(
                (
                    "admin_dialog_sender_client"
                    if (
                        message.sender_user_id
                        == message.client_user_id
                    )
                    else "admin_dialog_sender_specialist"
                ),
                language,
            )

            history_lines.append(
                f"{sender_label}: {message_text}"
            )

        history = "\n".join(history_lines)

    selected_context = (
        contexts[index]
        if index < len(contexts)
        else {}
    )

    context_parts = []

    if selected_context.get("has_complaint"):
        context_parts.append(
            t(
                "admin_dialog_context_complaint",
                language,
            )
        )

    if selected_context.get("has_risk_flag"):
        context_parts.append(
            t(
                "admin_dialog_context_risk",
                language,
            )
        )

    context_label = " + ".join(context_parts) or "—"

    screen_text = t(
        "admin_dialog_detail",
        language,
    ).format(
        number=index + 1,
        context=context_label,
        status=thread_status,
        history=history,
    )

    await callback.message.answer(
        screen_text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_dialog_back_to_list_btn",
                            language,
                        ),
                        callback_data="ADM_DIALOGS_STUB",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_panel_back",
                            language,
                        ),
                        callback_data="ADM_PANEL",
                    )
                ],
            ]
        ),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_PROMOTION_STUB"
)
async def admin_promotion_section_stub(
    callback: CallbackQuery,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await callback.message.answer(
        t(
            "admin_section_stub",
            language,
        ).format(
            section=t(
                "admin_promotion_section_btn",
                language,
            ),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_panel_back",
                            language,
                        ),
                        callback_data="ADM_PANEL",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "search_menu",
                            language,
                        ),
                        callback_data="MAIN_MENU",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

def format_admin_category_card(
    item,
    language: str,
) -> str:
    return t("admin_dict_category_card", language).format(
        title=item.title,
        code=item.code,
        status=item.status,
        sort_order=item.sort_order,
        professions=item.professions_count,
        specialists=item.specialists_count,
        release=item.release or "-",
    )

def format_admin_category_specialists_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_category_specialists_empty", language)

    lines = [
        t("admin_dict_category_specialists_title", language).format(
            count=len(items),
        )
    ]

    start_number = page * ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE + 1

    for index, item in enumerate(items, start=start_number):
        verified = "yes" if item.is_verified else "no"
        available = "yes" if item.is_available else "no"

        lines.append(
            t("admin_dict_category_specialist_row", language).format(
                number=index,
                name=item.display_name,
                status=item.status,
                professions=item.profession_names,
                verified=verified,
                available=available,
            )
        )

    return "\n\n".join(lines)

def format_admin_profession_specialists_list(
    items,
    *,
    page: int,
    language: str,
) -> str:
    if not items:
        return t("admin_dict_profession_specialists_empty", language)

    lines = [
        t("admin_dict_profession_specialists_title", language).format(
            page=page + 1,
            count=len(items),
        )
    ]

    start_number = page * ADMIN_PROFESSION_SPECIALISTS_PAGE_SIZE + 1

    for index, item in enumerate(items, start=start_number):
        verified = "yes" if item.is_verified else "no"
        available = "yes" if item.is_available else "no"

        lines.append(
            t("admin_dict_category_specialist_row", language).format(
                number=index,
                name=item.display_name,
                status=item.status,
                professions=item.profession_names,
                verified=verified,
                available=available,
            )
        )

    return "\n\n".join(lines)


def admin_profession_specialists_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t("admin_dict_specialist_move_btn", language),
                callback_data="ADM_SPEC_MOVE_SELECT",
            )
        ],
        [
            InlineKeyboardButton(
                text=t("admin_dict_specialist_move_all_btn", language),
                callback_data="ADM_SPEC_MOVE_ALL",
            )
        ],
    ]

    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_PROF_SPECIALISTS:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_PROF_SPECIALISTS:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_PROFESSIONS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_category_specialists_keyboard(
    *,
    page: int,
    has_next: bool,
    language: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_specialist_move_btn",
                    language,
                ),
                callback_data="ADM_CAT_SPEC_MOVE_SELECT",
            )
        ],
        [
            InlineKeyboardButton(
                text=t(
                    "admin_dict_category_specialist_move_all_btn",
                    language,
                ),
                callback_data="ADM_CAT_SPEC_MOVE_ALL",
            )
        ],
    ]
    paging_row = []

    if page > 0:
        paging_row.append(
            InlineKeyboardButton(
                text=t("back", language),
                callback_data=f"ADM_CAT_SPECIALISTS:{page - 1}",
            )
        )

    if has_next:
        paging_row.append(
            InlineKeyboardButton(
                text=t("admin_next", language),
                callback_data=f"ADM_CAT_SPECIALISTS:{page + 1}",
            )
        )

    if paging_row:
        rows.append(paging_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_CATEGORIES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_category_card_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_rename_btn", language),
                    callback_data="ADM_CAT_RENAME",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_toggle_btn", language),
                    callback_data="ADM_CAT_TOGGLE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_archive_btn", language),
                    callback_data="ADM_CAT_ARCHIVE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_reorder_btn", language),
                    callback_data="ADM_CAT_REORDER",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_profession_create_btn", language),
                    callback_data="ADM_PROF_CREATE",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_dict_category_specialists_btn", language),
                    callback_data="ADM_CAT_SPECIALISTS",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("admin_panel_back", language),
                    callback_data="ADM_DICT_CATEGORIES",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("search_menu", language),
                    callback_data="MAIN_MENU",
                )
            ],
        ]
    )

@admin_router.callback_query(
    F.data == "ADM_CAT_SPEC_MOVE_SELECT"
)
async def admin_category_specialist_move_select_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    specialist_ids = data.get(
        "admin_category_specialist_ids"
    ) or []

    if not specialist_ids:
        await callback.answer(
            t("admin_dict_specialist_move_empty", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .entering_admin_category_specialist_move_numbers
    )
    page = int(
        data.get("admin_category_specialists_page") or 0
    )
    start_number = (
        page * ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE + 1
    )
    end_number = start_number + len(specialist_ids) - 1

    example_numbers = list(
        range(
            start_number,
            min(end_number, start_number + 2) + 1,
        )
    )

    await callback.message.answer(
        t(
            "admin_dict_specialist_move_select_page_prompt",
            language,
        ).format(
            start=start_number,
            end=end_number,
            example=",".join(
                str(number)
                for number in example_numbers
            ),
        )
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_CAT_SPEC_MOVE_ALL"
)
async def admin_category_specialist_move_all(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    category_id = data.get(
        "admin_selected_category_id"
    )

    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            specialist_ids = await DictionaryService(
                DictionaryRepository(session)
            ).list_category_specialist_ids(
                category_id=category_id,
            )

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    if not specialist_ids:
        await callback.answer(
            t("admin_dict_specialist_move_empty", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_category_specialist_move_ids=(
            specialist_ids
        ),
        admin_move_source_type="category",
        admin_move_source_id=category_id,
        admin_move_specialist_ids=specialist_ids,
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_target_profession_ids=[],
        admin_move_mode=None,
    )
    await show_admin_multi_move_categories(
        callback.message,
        state,
        language,
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM
    .entering_admin_category_specialist_move_numbers
)
async def admin_category_specialist_move_numbers_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    data = await state.get_data()

    specialist_ids = data.get(
        "admin_category_specialist_ids"
    ) or []
    page = int(
        data.get("admin_category_specialists_page") or 0
    )

    if not specialist_ids:
        await state.clear()
        await message.answer(
            t("admin_dict_specialist_move_empty", language)
        )
        return

    start_number = (
        page * ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE + 1
    )
    end_number = start_number + len(specialist_ids) - 1

    raw_numbers = [
        item.strip()
        for item in (
            message.text or ""
        ).replace(";", ",").split(",")
        if item.strip()
    ]

    try:
        entered_numbers = [
            int(item)
            for item in raw_numbers
        ]
    except ValueError:
        await message.answer(
            t(
                "admin_dict_specialist_move_bad_page_numbers",
                language,
            ).format(
                start=start_number,
                end=end_number,
            )
        )
        return

    if (
        not entered_numbers
        or any(
            number < start_number
            or number > end_number
            for number in entered_numbers
        )
    ):
        await message.answer(
            t(
                "admin_dict_specialist_move_bad_page_numbers",
                language,
            ).format(
                start=start_number,
                end=end_number,
            )
        )
        return

    selected_specialist_ids = [
        specialist_ids[number - start_number]
        for number in dict.fromkeys(entered_numbers)
    ]

    await state.update_data(
        admin_selected_category_specialist_move_ids=(
            selected_specialist_ids
        ),
        admin_move_source_type="category",
        admin_move_source_id=data.get(
            "admin_selected_category_id"
        ),
        admin_move_specialist_ids=(
            selected_specialist_ids
        ),
        admin_move_target_category_id=None,
        admin_move_target_category_candidate_ids=[],
        admin_move_target_profession_ids=[],
        admin_move_mode=None,
    )
    await show_admin_multi_move_categories(
        message,
        state,
        language,
    )

def admin_category_specialist_move_confirm_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_confirm_btn",
                        language,
                    ),
                    callback_data=(
                        "ADM_CAT_SPEC_MOVE_CONFIRM"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_dict_specialist_move_cancel_btn",
                        language,
                    ),
                    callback_data=(
                        "ADM_CAT_SPEC_MOVE_CANCEL"
                    ),
                )
            ],
        ]
    )


@admin_router.message(
    AdminModerationFSM
    .entering_admin_category_specialist_move_target
)
async def admin_category_specialist_move_target_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    data = await state.get_data()

    source_category_id = data.get(
        "admin_selected_category_id"
    )
    specialist_ids = data.get(
        "admin_selected_category_specialist_move_ids"
    ) or []
    candidate_ids = data.get(
        "admin_category_specialist_move_target_candidate_ids"
    ) or []

    if not source_category_id or not specialist_ids:
        await state.clear()
        await message.answer(
            t("admin_dict_specialist_move_empty", language)
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.clear()
        await message.answer(
            t("admin_access_denied", language)
        )
        return

    entered_value = " ".join(
        (message.text or "").split()
    )
    target_profession_id = None

    try:
        async with get_session() as session:
            service = DictionaryService(
                DictionaryRepository(session)
            )

            if candidate_ids and entered_value.isdigit():
                selected_index = int(entered_value) - 1

                if (
                    selected_index < 0
                    or selected_index >= len(candidate_ids)
                ):
                    await message.answer(
                        t(
                            "admin_dict_specialist_move_target_bad_number",
                            language,
                        ).format(
                            count=len(candidate_ids),
                        )
                    )
                    return

                target_profession_id = candidate_ids[
                    selected_index
                ]

            else:
                targets = (
                    await service
                    .find_specialist_move_targets(
                        title=entered_value,
                        language=language,
                    )
                )

                if len(targets) > 1:
                    await state.update_data(
                        admin_category_specialist_move_target_candidate_ids=[
                            str(target.profession_id)
                            for target in targets
                        ]
                    )

                    choices = [
                        t(
                            "admin_dict_specialist_move_target_multiple",
                            language,
                        ),
                        "",
                    ]

                    for index, target in enumerate(
                        targets,
                        start=1,
                    ):
                        choices.append(
                            f"{index}. {target.title} | "
                            f"{target.category_name}"
                        )

                    await message.answer(
                        "\n".join(choices)
                    )
                    return

                target_profession_id = str(
                    targets[0].profession_id
                )

            preview = (
                await service
                .preview_category_specialist_move(
                    source_category_id=source_category_id,
                    target_profession_id=target_profession_id,
                    specialist_ids=specialist_ids,
                    language=language,
                )
            )

    except DictionaryServiceError as exc:
        await message.answer(
            t(exc.text_key, language)
        )
        return

    await state.update_data(
        admin_category_specialist_move_target_id=(
            target_profession_id
        ),
        admin_category_specialist_move_target_candidate_ids=[],
    )
    await state.set_state(
        AdminModerationFSM
        .confirming_admin_category_specialist_move
    )

    await message.answer(
        t(
            "admin_dict_category_specialist_move_preview",
            language,
        ).format(
            source_category=preview.source_category.title,
            target_profession=(
                preview.target_profession.title
            ),
            target_category=(
                preview.target_profession.category_name
            ),
            count=len(preview.selected_specialists),
        ),
        reply_markup=(
            admin_category_specialist_move_confirm_keyboard(
                language
            )
        ),
    )


@admin_router.callback_query(
    F.data == "ADM_CAT_SPEC_MOVE_CANCEL"
)
async def admin_category_specialist_move_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await state.update_data(
        admin_category_specialist_move_target_id=None,
        admin_category_specialist_move_target_candidate_ids=[],
    )
    await state.set_state(
        AdminModerationFSM
        .entering_admin_category_specialist_move_target
    )

    await callback.message.answer(
        t("admin_dict_specialist_move_target_prompt", language)
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_CAT_SPEC_MOVE_CONFIRM"
)
async def admin_category_specialist_move_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    source_category_id = data.get(
        "admin_selected_category_id"
    )
    target_profession_id = data.get(
        "admin_category_specialist_move_target_id"
    )
    specialist_ids = data.get(
        "admin_selected_category_specialist_move_ids"
    ) or []

    if (
        not source_category_id
        or not target_profession_id
        or not specialist_ids
    ):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await DictionaryService(
                DictionaryRepository(session)
            ).move_category_specialists_to_profession(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                source_category_id=source_category_id,
                target_profession_id=target_profession_id,
                specialist_ids=specialist_ids,
                language=language,
            )

            await session.commit()

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.clear()

    await callback.message.answer(
        t(
            "admin_dict_category_specialist_move_done",
            language,
        ).format(
            target_profession=(
                result.target_profession.title
            ),
            target_category=(
                result.target_profession.category_name
            ),
            moved_count=result.moved_count,
            duplicate_count=(
                result.archived_duplicate_count
            ),
            extra_links_count=(
                result.archived_extra_links_count
            ),
            synchronized_count=(
                result.synchronized_primary_count
            ),
            missing_count=result.missing_count,
        ),
        reply_markup=admin_dictionaries_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_CAT_SPECIALISTS")
@admin_router.callback_query(F.data.startswith("ADM_CAT_SPECIALISTS:"))
async def admin_category_specialists(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    page = 0
    if callback.data and ":" in callback.data:
        try:
            page = max(0, int(callback.data.split(":", 1)[1]))
        except ValueError:
            page = 0

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            items = await DictionaryService(
                DictionaryRepository(session)
            ).list_category_specialists(
                category_id=category_id,
                limit=ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE + 1,
                offset=page * ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE,
            )

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    has_next = len(items) > ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE
    visible_items = items[:ADMIN_CATEGORY_SPECIALISTS_PAGE_SIZE]

    await state.update_data(
        admin_category_specialist_ids=[
            str(item.specialist_id)
            for item in visible_items
        ],
        admin_category_specialists_page=page,
    )

    await callback.message.answer(
        format_admin_category_specialists_list(
            visible_items,
            page=page,
            language=language,
        ),
        reply_markup=admin_category_specialists_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.message(AdminModerationFSM.entering_admin_category_number)
async def admin_category_open_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    category_ids = data.get("admin_category_ids") or []

    try:
        index = int((message.text or "").strip()) - 1
    except ValueError:
        await message.answer(
            t("admin_dict_category_open_bad_number", language).format(
                count=len(category_ids),
            )
        )
        return

    if index < 0 or index >= len(category_ids):
        await message.answer(
            t("admin_dict_category_open_bad_number", language).format(
                count=len(category_ids),
            )
        )
        return

    category_id = category_ids[index]

    async with get_session() as session:
        item = await DictionaryService(
            DictionaryRepository(session)
        ).get_category_card(
            category_id=category_id,
            language=language,
        )

    if not item:
        await message.answer(t("admin_item_not_found", language))
        await state.clear()
        return

    await state.update_data(
        admin_selected_category_id=category_id,
    )
    await state.set_state(None)

    await message.answer(
        format_admin_category_card(item, language),
        reply_markup=admin_category_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_CAT_RENAME")
async def admin_category_rename_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    if not data.get("admin_selected_category_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_category_rename)
    await callback.message.answer(
        t("admin_dict_category_rename_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_category_rename)
async def admin_category_rename_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    if not category_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).rename_category(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                category_id=category_id,
                title=message.text or "",
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_selected_category_id=str(item.category_id),
    )
    await state.set_state(None)

    await message.answer(
        t("admin_dict_category_rename_done", language),
    )
    await message.answer(
        format_admin_category_card(item, language),
        reply_markup=admin_category_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "ADM_CAT_TOGGLE")
async def admin_category_toggle_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).toggle_category_visibility(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                category_id=category_id,
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_category_id=str(item.category_id),
    )

    await callback.message.answer(
        t("admin_dict_category_visibility_done", language),
    )
    await callback.message.answer(
        format_admin_category_card(item, language),
        reply_markup=admin_category_card_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_CAT_ARCHIVE")
async def admin_category_archive(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    if not category_id:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            service = DictionaryService(DictionaryRepository(session))
            current_item = await service.get_category_card(
                category_id=category_id,
                language=language,
            )

            if not current_item:
                raise DictionaryServiceError("admin_item_not_found")

            if current_item.status_code == "archived":
                item = await service.unarchive_category(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    category_id=category_id,
                    language=language,
                )
                done_text_key = "admin_dict_category_unarchive_done"
            else:
                item = await service.archive_category(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    category_id=category_id,
                    language=language,
                )
                done_text_key = "admin_dict_category_archive_done"

            await session.commit()

    except DictionaryServiceError as exc:
        await callback.answer(
            t(exc.text_key, language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_selected_category_id=str(item.category_id),
    )

    await callback.message.answer(
        t(done_text_key, language),
    )
    await callback.message.answer(
        format_admin_category_card(item, language),
        reply_markup=admin_category_card_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_CAT_REORDER")
async def admin_category_sort_order_prompt(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    if not data.get("admin_selected_category_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(AdminModerationFSM.entering_admin_category_sort_order)
    await callback.message.answer(
        t("admin_dict_category_sort_order_prompt", language)
    )
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_admin_category_sort_order)
async def admin_category_sort_order_receive(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)

    data = await state.get_data()
    category_id = data.get("admin_selected_category_id")

    if not category_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        message.from_user.id
    )

    if "super_admin" not in roles:
        await state.clear()
        await message.answer(t("admin_access_denied", language))
        return

    try:
        async with get_session() as session:
            item = await DictionaryService(
                DictionaryRepository(session)
            ).update_category_sort_order(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                category_id=category_id,
                sort_order_text=message.text or "",
                language=language,
            )
            await session.commit()

    except DictionaryServiceError as exc:
        await message.answer(t(exc.text_key, language))
        return

    await state.update_data(
        admin_selected_category_id=str(item.category_id),
    )
    await state.set_state(None)

    await message.answer(
        t("admin_dict_category_sort_order_done", language),
    )
    await message.answer(
        format_admin_category_card(item, language),
        reply_markup=admin_category_card_keyboard(language),
    )

@admin_router.callback_query(F.data == "SA_PANEL")
async def super_admin_panel_callback(
    callback: CallbackQuery,
    state: FSMContext,
):
    await show_admin_panel(callback, state)


@admin_router.callback_query(F.data.in_({"SA_FINANCE", "SA_REGIONS"}))
async def super_admin_disabled_external_sections(
    callback: CallbackQuery,
):
    language = normalize_language(callback.from_user.language_code)

    await callback.answer(
        t("feature_disabled_beta_message", language),
        show_alert=True,
    )


@admin_router.callback_query(F.data == "SA_ROLES")
async def super_admin_roles_entry(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or not tenant_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if data.get("super_admin_selected_user_id"):
        await super_admin_user_roles(callback, state)
        return

    await state.set_state(AdminModerationFSM.waiting_super_admin_user_search)

    await callback.message.answer(
        t("super_admin_user_search_prompt", language)
    )
    await callback.answer()

@admin_router.callback_query(F.data == "SA_USER_PROFILE")
async def super_admin_user_profile_alias(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    (
        admin_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or "super_admin" not in roles
    ):
        await callback.answer(
            t(
                "admin_access_denied",
                language,
            ),
            show_alert=True,
        )
        return

    data = await state.get_data()
    target_user_id_raw = data.get(
        "super_admin_selected_user_id"
    )

    if not target_user_id_raw:
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(
            target_user_id_raw
        )

        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_super_admin_user_details(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )

    except (
        ModerationError,
        ValueError,
    ) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_super_admin_user_card(
            card,
            language,
        ),
        reply_markup=super_admin_user_card_keyboard(
            language,
        ),
    )
    await callback.answer()
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()
    target_user_id_raw = data.get("super_admin_selected_user_id")

    if not target_user_id_raw:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await callback.answer(
        t("feature_disabled_beta_message", language),
        show_alert=True,
    )


@admin_router.callback_query(F.data == "SA_USER_AUDIT")
async def super_admin_user_audit_alias(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()

    if not data.get("super_admin_selected_user_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await open_super_admin_audit_queue(
        callback,
        state,
        target_type="user",
        page=0,
    )


@admin_router.callback_query(F.data == "SA_USER_GLOBAL_BLACKLIST")
async def super_admin_user_global_blacklist_alias(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()

    if not data.get("super_admin_selected_user_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await open_super_admin_global_blacklist_queue(
        callback,
        state,
        view="active",
        page=0,
    )

@admin_router.callback_query(F.data == "SA_ROLE_SCOPE")
async def super_admin_role_scope_alias(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()

    if not data.get("super_admin_selected_user_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await open_super_admin_role_scopes_queue(
        callback,
        state,
        view="active",
        page=0,
        user_filtered=True,
    )


@admin_router.callback_query(F.data == "SA_ROLE_HISTORY")
async def super_admin_role_history_alias(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    data = await state.get_data()

    if not data.get("super_admin_selected_user_id"):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await open_super_admin_audit_queue(
        callback,
        state,
        target_type="user",
        page=0,
    )

@admin_router.callback_query(
    F.data == "ADM_MODERATION_MENU"
)
async def open_admin_moderation_menu(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(
            {"admin", "super_admin"}
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            summary = await ModerationService(
                ModerationRepository(session)
            ).open_moderator_menu(
                moderator_user_id=admin_user_id,
                tenant_id=tenant_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.clear()

    await callback.message.answer(
        format_moderator_menu(
            summary,
            language,
        ),
        reply_markup=moderator_menu_keyboard(
            summary,
            language,
            show_role_switch=False,
            show_specialist_management=bool(
                roles.intersection({"admin", "super_admin"})
            ),
            back_callback="ADM_PANEL",
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_USERS")
async def ask_admin_user_search(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(
        AdminModerationFSM.entering_admin_user_search
    )

    await callback.message.answer(
        t("admin_user_search_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("admin_panel_back", language),
                        callback_data="ADM_PANEL",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM.entering_admin_user_search
)
async def receive_admin_user_search(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    query = (message.text or "").strip()

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(message.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await state.clear()
        await message.answer(
            t("admin_access_denied", language)
        )
        return

    try:
        async with get_session() as session:
            cards = await ModerationService(
                ModerationRepository(session)
            ).search_admin_users(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                query=query,
            )
    except ModerationError as exc:
        await message.answer(
            t("admin_user_search_error", language).format(
                error=str(exc),
            )
        )
        return

    await state.set_state(None)

    if not cards:
        await state.update_data(
            admin_user_search_ids=[],
        )
        await message.answer(
            t("admin_user_search_empty", language),
            reply_markup=admin_user_search_actions_keyboard(
                language
            ),
        )
        return

    await state.update_data(
        admin_user_search_ids=[
            str(card.user_id)
            for card in cards
        ],
    )

    await message.answer(
        t("admin_user_search_results", language).format(
            count=len(cards),
        )
    )

    for index, card in enumerate(cards):
        await message.answer(
            format_admin_user_search_card(
                card,
                number=index + 1,
                language=language,
            ),
            reply_markup=admin_user_search_result_keyboard(
                index=index,
                language=language,
            ),
        )

    await message.answer(
        t("admin_user_search_actions", language),
        reply_markup=admin_user_search_actions_keyboard(
            language
        ),
    )

@admin_router.callback_query(
    F.data.startswith("ADM_USER_VIEW:")
)
async def open_admin_user_details(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("admin_user_search_ids") or []

    if index < 0 or index >= len(user_ids):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_admin_user_details(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_user_selected_index=index,
    )

    await callback.message.answer(
        format_admin_user_details(card, language),
        reply_markup=admin_user_details_keyboard(
            index=index,
            is_global_blacklisted=(
                card.is_global_blacklisted
            ),
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_USER_GLOBAL_UNBLOCK:")
)
async def ask_admin_user_global_unblock_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("admin_user_search_ids") or []

    if index < 0 or index >= len(user_ids):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_user_global_unblock_index=index,
        admin_user_global_unblock_reason=None,
    )
    await state.set_state(
        AdminModerationFSM
        .entering_admin_user_global_unblock_reason
    )

    await callback.message.answer(
        t(
            "admin_user_global_unblock_reason_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data=(
                            f"ADM_USER_GLOBAL_UNBLOCK_CANCEL:{index}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM
    .entering_admin_user_global_unblock_reason
)
async def receive_admin_user_global_unblock_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(
            t("admin_reason_too_short", language)
        )
        return

    data = await state.get_data()
    index = data.get("admin_user_global_unblock_index")
    user_ids = data.get("admin_user_search_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
    ):
        await state.clear()
        await message.answer(
            t("admin_user_not_found", language)
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.clear()
        await message.answer(
            t("admin_user_not_found", language)
        )
        return

    await state.update_data(
        admin_user_global_unblock_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM
        .confirming_admin_user_global_unblock
    )

    await message.answer(
        t(
            "admin_user_global_unblock_confirmation",
            language,
        ).format(
            user_number=f"user-{target_user_id.hex[:8]}",
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_unblock_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_USER_GLOBAL_UNBLOCK_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_USER_GLOBAL_UNBLOCK:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data=(
                            f"ADM_USER_GLOBAL_UNBLOCK_CANCEL:{index}"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    AdminModerationFSM
    .confirming_admin_user_global_unblock,
    F.data == "ADM_USER_GLOBAL_UNBLOCK_CONFIRM",
)
async def confirm_admin_user_global_unblock_first(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("admin_user_global_unblock_index")
    reason = data.get("admin_user_global_unblock_reason")
    user_ids = data.get("admin_user_search_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
        or not reason
    ):
        await state.clear()
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.clear()
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .confirming_admin_user_global_unblock_final
    )

    await callback.message.answer(
        t(
            "admin_user_global_unblock_final_confirmation",
            language,
        ).format(
            user_number=f"user-{target_user_id.hex[:8]}",
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_unblock_final_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_USER_GLOBAL_UNBLOCK_FINAL_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_USER_GLOBAL_UNBLOCK:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data=(
                            f"ADM_USER_GLOBAL_UNBLOCK_CANCEL:{index}"
                        ),
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    AdminModerationFSM
    .confirming_admin_user_global_unblock_final,
    F.data == "ADM_USER_GLOBAL_UNBLOCK_FINAL_CONFIRM",
)
async def execute_admin_user_global_unblock(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("admin_user_global_unblock_index")
    reason = data.get("admin_user_global_unblock_reason")
    user_ids = data.get("admin_user_search_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
    ):
        await state.clear()
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.clear()
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).unblock_user(
                admin_user_id=admin_user_id,
                user_id=target_user_id,
                reason=reason,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_user_global_unblock_reason=None,
        admin_user_global_unblock_index=None,
    )

    await callback.message.answer(
        t(
            "admin_user_global_unblock_completed",
            language,
        ).format(status=result.status),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_back_to_card_btn",
                            language,
                        ),
                        callback_data=f"ADM_USER_VIEW:{index}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_USER_GLOBAL_UNBLOCK_CANCEL:")
)
async def cancel_admin_user_global_unblock(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        index = 0

    await state.set_state(None)
    await state.update_data(
        admin_user_global_unblock_reason=None,
        admin_user_global_unblock_index=None,
    )

    await callback.message.answer(
        t(
            "admin_user_global_unblock_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_back_to_card_btn",
                            language,
                        ),
                        callback_data=f"ADM_USER_VIEW:{index}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_USER_GLOBAL_BLOCK:")
)
async def ask_admin_user_global_block_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("admin_user_search_ids") or []

    if index < 0 or index >= len(user_ids):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_user_global_block_index=index,
        admin_user_global_block_reason=None,
    )
    await state.set_state(
        AdminModerationFSM
        .entering_admin_user_global_block_reason
    )

    await callback.message.answer(
        t("admin_user_global_block_reason_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data=(
                            f"ADM_USER_GLOBAL_BLOCK_CANCEL:{index}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM
    .entering_admin_user_global_block_reason
)
async def receive_admin_user_global_block_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(
            t("admin_reason_too_short", language)
        )
        return

    data = await state.get_data()
    index = data.get("admin_user_global_block_index")
    user_ids = data.get("admin_user_search_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
    ):
        await state.clear()
        await message.answer(
            t("admin_user_not_found", language)
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.clear()
        await message.answer(
            t("admin_user_not_found", language)
        )
        return

    await state.update_data(
        admin_user_global_block_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_admin_user_global_block
    )

    await message.answer(
        t("admin_user_global_block_confirmation", language).format(
            user_number=f"user-{target_user_id.hex[:8]}",
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_block_confirm_btn",
                            language,
                        ),
                        callback_data="ADM_USER_GLOBAL_BLOCK_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_USER_GLOBAL_BLOCK:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data=(
                            f"ADM_USER_GLOBAL_BLOCK_CANCEL:{index}"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    AdminModerationFSM.confirming_admin_user_global_block,
    F.data == "ADM_USER_GLOBAL_BLOCK_CONFIRM",
)
async def confirm_admin_user_global_block_first(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("admin_user_global_block_index")
    reason = data.get("admin_user_global_block_reason")
    user_ids = data.get("admin_user_search_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
        or not reason
    ):
        await state.clear()
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.clear()
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .confirming_admin_user_global_block_final
    )

    await callback.message.answer(
        t(
            "admin_user_global_block_final_confirmation",
            language,
        ).format(
            user_number=f"user-{target_user_id.hex[:8]}",
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_global_block_final_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_USER_GLOBAL_BLOCK_FINAL_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_change_reason_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_USER_GLOBAL_BLOCK:{index}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data=(
                            f"ADM_USER_GLOBAL_BLOCK_CANCEL:{index}"
                        ),
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    AdminModerationFSM.confirming_admin_user_global_block_final,
    F.data == "ADM_USER_GLOBAL_BLOCK_FINAL_CONFIRM",
)
async def execute_admin_user_global_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    index = data.get("admin_user_global_block_index")
    reason = data.get("admin_user_global_block_reason")
    user_ids = data.get("admin_user_search_ids") or []

    if (
        not isinstance(index, int)
        or index < 0
        or index >= len(user_ids)
    ):
        await state.clear()
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await state.clear()
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).block_user(
                admin_user_id=admin_user_id,
                user_id=target_user_id,
                reason=reason,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_user_global_block_reason=None,
    )

    await callback.message.answer(
        t("admin_user_global_block_completed", language).format(
            status=result.status,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_back_to_card_btn",
                            language,
                        ),
                        callback_data=f"ADM_USER_VIEW:{index}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_USER_GLOBAL_BLOCK_CANCEL:")
)
async def cancel_admin_user_global_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        index = 0

    await state.set_state(None)
    await state.update_data(
        admin_user_global_block_reason=None,
        admin_user_global_block_index=None,
    )

    await callback.message.answer(
        t("admin_user_global_block_cancelled", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_user_back_to_card_btn",
                            language,
                        ),
                        callback_data=f"ADM_USER_VIEW:{index}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_USER_ROLES:")
)
async def open_admin_user_roles(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("admin_user_search_ids") or []

    if index < 0 or index >= len(user_ids):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_admin_user_details(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_admin_user_roles(card, language),
        reply_markup=admin_user_roles_keyboard(
            index=index,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_USER_HISTORY:")
)
async def open_admin_user_history(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    user_ids = data.get("admin_user_search_ids") or []

    if index < 0 or index >= len(user_ids):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    try:
        target_user_id = UUID(user_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_user_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            history = await ModerationService(
                ModerationRepository(session)
            ).list_admin_user_history(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_user_id=target_user_id,
                limit=10,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("admin_user_history_title", language).format(
            user_number=f"user-{target_user_id.hex[:8]}",
            count=len(history),
        )
    )

    if not history:
        await callback.message.answer(
            t("admin_user_history_empty", language),
            reply_markup=admin_user_history_keyboard(
                index=index,
                language=language,
            ),
        )
        await callback.answer()
        return

    for number, card in enumerate(history, start=1):
        await callback.message.answer(
            format_admin_user_history_item(
                card,
                number=number,
                language=language,
            )
        )

    await callback.message.answer(
        t("admin_user_history_actions", language),
        reply_markup=admin_user_history_keyboard(
            index=index,
            language=language,
        ),
    )
    await callback.answer()

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
async def admin_audit_panel(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_admin_audit_queue(
        callback,
        state,
        target_type="all",
        page=0,
    )


@admin_router.callback_query(
    F.data.startswith("ADM_AUDIT_QUEUE:")
)
async def change_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    if len(parts) != 3:
        await callback.answer()
        return

    target_type = parts[1]

    try:
        page = max(0, int(parts[2]))
    except ValueError:
        page = 0

    await open_admin_audit_queue(
        callback,
        state,
        target_type=target_type,
        page=page,
    )


@admin_router.callback_query(
    F.data == "ADM_AUDIT_FILTER"
)
async def open_admin_audit_filter(
    callback: CallbackQuery,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(
            {"admin", "super_admin"}
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("admin_audit_filter_title", language),
        reply_markup=admin_audit_filter_keyboard(language),
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("ADM_AUDIT_OPEN:")
)
async def open_admin_audit_details(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int((callback.data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    action_ids = data.get("admin_audit_action_ids") or []
    target_type = data.get("admin_audit_target_type") or "all"
    page = int(data.get("admin_audit_page") or 0)

    if index < 0 or index >= len(action_ids):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    try:
        action_id = UUID(action_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(
            {"admin", "super_admin"}
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_admin_audit_card(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                action_id=action_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("admin_audit_details", language).format(
            date=card.date,
            actor=card.actor,
            action=card.action,
            target=card.target,
            target_type=card.target_type,
            reason=card.reason,
            source=card.source,
        ),
        reply_markup=admin_audit_details_keyboard(
            target_type=target_type,
            page=page,
            language=language,
        ),
    )
    await callback.answer()

async def open_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    target_type: str,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(
            {"admin", "super_admin"}
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).open_admin_audit(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                target_type=target_type,
                page=page,
                page_size=ADMIN_AUDIT_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_audit_action_ids=[
            str(card.action_id)
            for card in result.items
        ],
        admin_audit_target_type=result.target_type,
        admin_audit_page=result.page,
    )

    await callback.message.answer(
        t("admin_audit_queue_title", language).format(
            filter=result.target_type,
            page=result.page + 1,
            count=len(result.items),
        )
    )

    if not result.items:
        await callback.message.answer(
            t("admin_audit_empty", language),
            reply_markup=admin_audit_queue_keyboard(
                target_type=result.target_type,
                page=result.page,
                has_next=False,
                language=language,
            ),
        )
        await callback.answer()
        return

    start_number = (
        result.page * ADMIN_AUDIT_PAGE_SIZE + 1
    )

    for offset, card in enumerate(result.items):
        await callback.message.answer(
            format_admin_audit_card(
                card,
                number=start_number + offset,
                language=language,
            ),
                reply_markup=super_admin_audit_card_keyboard(
                    index=offset,
                    language=language,
                ),
        )

    await callback.message.answer(
        t("admin_audit_actions_title", language),
        reply_markup=admin_audit_queue_keyboard(
            target_type=result.target_type,
            page=result.page,
            has_next=result.has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "SA_AUDIT")
async def super_admin_audit_panel(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_super_admin_audit_queue(
        callback,
        state,
        target_type="all",
        page=0,
    )


@admin_router.callback_query(F.data.startswith("SA_AUDIT_QUEUE:"))
async def change_super_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    if len(parts) != 3:
        await callback.answer()
        return

    target_type = parts[1]

    try:
        page = max(0, int(parts[2]))
    except ValueError:
        page = 0

    await open_super_admin_audit_queue(
        callback,
        state,
        target_type=target_type,
        page=page,
    )


@admin_router.callback_query(F.data == "SA_AUDIT_FILTER")
async def open_super_admin_audit_filter(
    callback: CallbackQuery,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("admin_audit_filter_title", language),
        reply_markup=super_admin_audit_filter_keyboard(language),
    )
    await callback.answer()


async def open_super_admin_audit_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    target_type: str,
    page: int,
):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).open_super_admin_audit(
                admin_user_id=admin_user_id,
                target_type=target_type,
                page=page,
                page_size=ADMIN_AUDIT_PAGE_SIZE,
            )

    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        super_admin_audit_action_ids=[
            str(card.action_id)
            for card in result.items
        ],
        super_admin_audit_target_type=result.target_type,
        super_admin_audit_page=result.page,
    )

    await callback.message.answer(
        t("admin_audit_queue_title", language).format(
            filter=result.target_type,
            page=result.page + 1,
            count=len(result.items),
        )
    )

    if not result.items:
        await callback.message.answer(
            t("admin_audit_empty", language),
            reply_markup=admin_audit_queue_keyboard(
                target_type=result.target_type,
                page=result.page,
                has_next=False,
                language=language,
                prefix="SA_AUDIT",
                back_callback="ADM_PANEL",
            ),
        )
        await callback.answer()
        return

    start_number = result.page * ADMIN_AUDIT_PAGE_SIZE + 1

    for offset, card in enumerate(result.items):
        await callback.message.answer(
            format_admin_audit_card(
                card,
                number=start_number + offset,
                language=language,
            ),
            reply_markup=super_admin_audit_card_keyboard(
                index=offset,
                language=language,
            ),
        )

    await callback.message.answer(
        t("admin_audit_actions_title", language),
        reply_markup=admin_audit_queue_keyboard(
            target_type=result.target_type,
            page=result.page,
            has_next=result.has_next,
            language=language,
            prefix="SA_AUDIT",
            back_callback="ADM_PANEL",
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("SA_AUDIT_OPEN:"))
async def open_super_admin_audit_details(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        index = int((callback.data or "").rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    action_ids = data.get("super_admin_audit_action_ids") or []
    target_type = data.get("super_admin_audit_target_type") or "all"
    page = int(data.get("super_admin_audit_page") or 0)

    if index < 0 or index >= len(action_ids):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    try:
        action_id = UUID(action_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_audit_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id or "super_admin" not in roles:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_super_admin_audit_event_detail(
                admin_user_id=admin_user_id,
                action_id=action_id,
            )

    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        t("super_admin_audit_event_detail", language).format(
            timestamp=card.timestamp,
            actor=card.actor,
            action=card.action,
            target_type=card.target_type,
            target=card.target,
            reason=card.reason,
            before_summary=card.before_summary,
            after_summary=card.after_summary,
            payload_summary=card.payload_summary,
            correlation_id=card.correlation_id,
            source=card.source,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("admin_audit_back_to_list_btn", language),
                        callback_data=f"SA_AUDIT_QUEUE:{target_type}:{page}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t("search_menu", language),
                        callback_data="MAIN_MENU",
                    )
                ],
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    (F.data == "ADM_ADMIN_SUPPORT")
    | F.data.startswith("ADM_ADMIN_SUPPORT:")
)
async def open_admin_escalated_tickets(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    page = 0

    if callback.data != "ADM_ADMIN_SUPPORT":
        try:
            page = max(
                int(callback.data.split(":", 1)[1]),
                0,
            )
        except (TypeError, ValueError, IndexError):
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            ticket_page = await SupportService(
                SupportRepository(session)
            ).list_admin_escalated_tickets(
                tenant_id=tenant_id,
                admin_user_id=admin_user_id,
                page=page,
                page_size=(
                    ADMIN_ESCALATED_TICKET_PAGE_SIZE
                ),
            )
    except SupportServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_support_ticket_ids=[
            str(ticket.id)
            for ticket in ticket_page.tickets
        ],
        admin_support_view="admin_escalated",
        admin_support_page=ticket_page.page,
    )

    await callback.message.answer(
        t("admin_escalated_tickets_header", language).format(
            page=ticket_page.page + 1,
            count=len(ticket_page.tickets),
        )
    )

    if not ticket_page.tickets:
        await callback.message.answer(
            t("admin_escalated_tickets_empty", language),
            reply_markup=admin_escalated_tickets_keyboard(
                page=ticket_page.page,
                has_next=False,
                language=language,
            ),
        )
        await callback.answer()
        return

    for index, ticket in enumerate(ticket_page.tickets):
        number = (
            ticket_page.page
            * ADMIN_ESCALATED_TICKET_PAGE_SIZE
            + index
            + 1
        )

        await callback.message.answer(
            format_admin_escalated_ticket(
                ticket,
                number=number,
                language=language,
            ),
            reply_markup=(
                admin_escalated_ticket_item_keyboard(
                    index=index,
                    language=language,
                )
            ),
        )

    await callback.message.answer(
        t("admin_escalated_tickets_actions", language),
        reply_markup=admin_escalated_tickets_keyboard(
            page=ticket_page.page,
            has_next=ticket_page.has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_ADMIN_SUPPORT_OPEN:")
)
async def open_admin_escalated_ticket_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    ticket_ids = data.get("admin_support_ticket_ids") or []
    page = int(data.get("admin_support_page") or 0)

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if index < 0 or index >= len(ticket_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            view = await SupportService(
                SupportRepository(session)
            ).get_admin_escalated_ticket_view(
                tenant_id=tenant_id,
                admin_user_id=admin_user_id,
                ticket_id=UUID(ticket_ids[index]),
            )
    except (ValueError, SupportServiceError) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_support_ticket_card(
            view,
            index=index,
            total=len(ticket_ids),
            language=language,
        ),
        reply_markup=admin_escalated_ticket_card_keyboard(
            index=index,
            page=page,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_ADMIN_TICKET_ACTION:")
)
async def ask_admin_ticket_action_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    parts = (callback.data or "").split(":")

    if len(parts) != 3 or parts[1] not in {
        "assign",
        "resolve",
    }:
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    action = parts[1]

    try:
        index = int(parts[2])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    data = await state.get_data()
    ticket_ids = data.get("admin_support_ticket_ids") or []

    if index < 0 or index >= len(ticket_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_ticket_action=action,
        admin_ticket_action_index=index,
        admin_ticket_action_id=ticket_ids[index],
    )
    await state.set_state(
        AdminModerationFSM.entering_admin_ticket_action_reason
    )

    await callback.message.answer(
        t("admin_ticket_action_reason_prompt", language).format(
            action=t(
                f"admin_ticket_action_{action}",
                language,
            ),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("cancel", language),
                        callback_data=(
                            f"ADM_ADMIN_SUPPORT_OPEN:{index}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM.entering_admin_ticket_action_reason
)
async def execute_admin_ticket_action(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(
            t("admin_reason_too_short", language)
        )
        return

    data = await state.get_data()
    action = data.get("admin_ticket_action")
    ticket_id = data.get("admin_ticket_action_id")
    index = int(data.get("admin_ticket_action_index") or 0)
    page = int(data.get("admin_support_page") or 0)

    if (
        action not in {"assign", "resolve"}
        or not ticket_id
    ):
        await state.clear()
        await message.answer(
            t("admin_item_not_found", language)
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(message.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await state.clear()
        await message.answer(
            t("admin_access_denied", language)
        )
        return

    try:
        async with get_session() as session:
            service = SupportService(
                SupportRepository(session)
            )

            if action == "assign":
                ticket = (
                    await service
                    .assign_admin_escalated_ticket(
                        tenant_id=tenant_id,
                        admin_user_id=admin_user_id,
                        ticket_id=UUID(ticket_id),
                        reason=reason,
                    )
                )
            else:
                ticket = (
                    await service
                    .resolve_admin_escalated_ticket(
                        tenant_id=tenant_id,
                        admin_user_id=admin_user_id,
                        ticket_id=UUID(ticket_id),
                        reason=reason,
                    )
                )
    except (ValueError, SupportServiceError) as exc:
        await message.answer(
            t("support_error", language).format(
                error=str(exc),
            )
        )
        return

    await state.set_state(None)
    await state.update_data(
        admin_ticket_action=None,
        admin_ticket_action_id=None,
        admin_ticket_action_index=None,
    )

    await message.answer(
        t("admin_ticket_action_completed", language).format(
            action=t(
                f"admin_ticket_action_{action}",
                language,
            ),
            status=t(
                f"support_status_{ticket.status}",
                language,
            ),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("admin_panel_back", language),
                        callback_data=(
                            f"ADM_ADMIN_SUPPORT:{page}"
                        ),
                    )
                ]
            ]
        ),
    )

@admin_router.callback_query(F.data == "ADM_SUPPORT")
async def open_support_staff_menu(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    async with get_session() as session:
        role_context = await UserService(session).get_role_switch_context(callback.from_user.id)
        show_role_switch = bool(
            role_context and len(role_context.available_roles) > 1
        )

        try:
            counts = await SupportService(
                SupportRepository(session)
            ).get_staff_ticket_counts(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                statuses={"open", "in_progress", "resolved"},
            )
        except SupportServiceError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        await EventRepository(session).create_event(
            event_type="support_menu",
            tenant_id=tenant_id,
            user_id=admin_user_id,
            entity_type="support_ticket",
            entity_id=None,
            payload={
                "source": "support_staff_menu",
                "counts": counts,
            },
            platform="telegram",
        )
        await session.commit()

    await state.clear()
    await callback.message.answer(
        format_support_staff_menu(counts, language),
        reply_markup=support_staff_menu_keyboard(
            language,
            show_role_switch=show_role_switch,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_SUPPORT_SEARCH")
async def ask_support_ticket_search(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    await state.set_state(AdminModerationFSM.entering_support_search)
    await callback.message.answer(
        t("support_staff_search_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("support_staff_back_to_panel", language),
                        callback_data="ADM_SUPPORT",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(AdminModerationFSM.entering_support_search)
async def receive_support_ticket_search(message: Message, state: FSMContext):
    language = normalize_language(message.from_user.language_code)
    query = (message.text or "").strip()

    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        async with get_session() as session:
            tickets = await SupportService(
                SupportRepository(session)
            ).search_staff_tickets(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                query=query,
                limit=SUPPORT_STAFF_PAGE_SIZE,
                offset=0,
            )

            await EventRepository(session).create_event(
                event_type="ticket_search",
                tenant_id=tenant_id,
                user_id=admin_user_id,
                entity_type="support_ticket",
                entity_id=None,
                payload={
                    "query": query,
                    "count": len(tickets),
                },
                platform="telegram",
            )
            await session.commit()
    except SupportServiceError as exc:
        await message.answer(t("support_error", language).format(error=str(exc)))
        return

    await state.update_data(
        admin_support_ticket_ids=[str(ticket.id) for ticket in tickets],
        admin_support_view="search",
        admin_support_page=0,
    )

    await message.answer(
        format_support_staff_search_header(
            tickets,
            query=query,
            language=language,
        )
    )

    for index, ticket in enumerate(tickets):
        await message.answer(
            format_support_staff_ticket_card(
                ticket,
                number=index + 1,
                language=language,
            ),
            reply_markup=support_staff_ticket_card_keyboard(
                index=index,
                ticket=ticket,
                language=language,
            ),
        )

    await message.answer(
        t("support_staff_list_actions", language),
        reply_markup=support_staff_ticket_actions_keyboard(
            view="open",
            page=0,
            has_next=False,
            language=language,
        ),
    )

    await state.set_state(None)

@admin_router.callback_query(F.data == "ADM_SUPPORT_FILTERS")
async def show_support_ticket_filters(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    await callback.message.answer(
        t("support_staff_filters_title", language),
        reply_markup=support_staff_filters_keyboard(language),
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_SUPPORT_VIEW:"))
async def list_support_tickets_by_status(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    try:
        _, view, page_raw = (callback.data or "").split(":", 2)
        page = max(0, int(page_raw))
    except (TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    statuses = support_staff_status_filter(view)

    try:
        async with get_session() as session:
            tickets = await SupportService(
                SupportRepository(session)
            ).list_staff_tickets(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                statuses=statuses,
                limit=SUPPORT_STAFF_PAGE_SIZE + 1,
                offset=page * SUPPORT_STAFF_PAGE_SIZE,
            )

            visible_tickets = tickets[:SUPPORT_STAFF_PAGE_SIZE]
            has_next = len(tickets) > SUPPORT_STAFF_PAGE_SIZE

            await EventRepository(session).create_event(
                event_type="ticket_list",
                tenant_id=tenant_id,
                user_id=admin_user_id,
                entity_type="support_ticket",
                entity_id=None,
                payload={
                    "source": "support_staff",
                    "view": view,
                    "page": page,
                    "count": len(visible_tickets),
                    "has_next": has_next,
                },
                platform="telegram",
            )
            await session.commit()
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await state.update_data(
        admin_support_ticket_ids=[str(ticket.id) for ticket in visible_tickets],
        admin_support_view=view,
        admin_support_page=page,
    )

    await callback.message.answer(
        format_support_staff_ticket_header(
            visible_tickets,
            view=view,
            page=page,
            has_next=has_next,
            language=language,
        )
    )

    for index, ticket in enumerate(visible_tickets):
        number = page * SUPPORT_STAFF_PAGE_SIZE + index + 1
        await callback.message.answer(
            format_support_staff_ticket_card(
                ticket,
                number=number,
                language=language,
            ),
            reply_markup=support_staff_ticket_card_keyboard(
                index=index,
                ticket=ticket,
                language=language,
            ),
        )

    await callback.message.answer(
        t("support_staff_list_actions", language),
        reply_markup=support_staff_ticket_actions_keyboard(
            view=view,
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_SUP_TAKE:"))
async def take_support_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_support_ticket_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    ticket_id = UUID(ids[index])

    try:
        async with get_session() as session:
            ticket = await SupportService(
                SupportRepository(session)
            ).update_ticket_status(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                ticket_id=ticket_id,
                status="in_progress",
            )

            await ModerationRepository(session).log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="ticket_assigned",
                entity_type="support_ticket",
                entity_id=ticket.id,
                payload={
                    "status": ticket.status,
                },
            )
            await session.commit()
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        t("support_staff_ticket_taken", language),
            reply_markup=support_staff_ticket_actions_keyboard(
                view=data.get("admin_support_view") or "open",
                page=int(data.get("admin_support_page") or 0),
                has_next=False,
                language=language,
            ),
    )
    await callback.answer()

async def show_support_ticket(callback: CallbackQuery, state: FSMContext, index: int):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_support_ticket_ids") or []

    if not ids:
        await callback.message.answer(
            t("admin_no_support_tickets", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    index = max(0, min(int(index), len(ids) - 1))
    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)

    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            view = await SupportService(
                SupportRepository(session)
            ).get_staff_ticket_view(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                ticket_id=UUID(ids[index]),
            )
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        format_support_ticket_card(
            view,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=support_ticket_keyboard(index, len(ids), language),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("ADM_SUP_VIEW:"))
async def view_support_ticket(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_support_ticket(callback, state, index=index)


@admin_router.callback_query(F.data.startswith("ADM_SUP_REPLY:"))
async def ask_support_reply(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_support_ticket_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    await state.update_data(
        admin_support_ticket_id=ids[index],
        admin_support_ticket_index=index,
    )
    await state.set_state(AdminModerationFSM.entering_support_reply)
    await callback.message.answer(t("admin_support_reply_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_support_reply)
async def receive_support_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(message.from_user.language_code)

    ticket_id = data.get("admin_support_ticket_id")
    index = int(data.get("admin_support_ticket_index") or 0)

    if not ticket_id:
        await message.answer(t("admin_item_not_found", language))
        await state.clear()
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        async with get_session() as session:
            reply_result = await SupportService(
                SupportRepository(session)
            ).add_staff_message(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                ticket_id=UUID(ticket_id),
                message_text=message.text or "",
            )
    except SupportServiceError as exc:
        await message.answer(t("support_error", language).format(error=str(exc)))
        return

    if reply_result.recipient_chat_id is not None:
        try:
            await message.bot.send_message(
                chat_id=reply_result.recipient_chat_id,
                text=t("support_staff_reply_received", language).format(
                    ticket_id=str(ticket_id)[:8],
                    message=message.text or "",
                ),
            )
        except Exception:
            logger.exception(
                "support_reply_notification_failed ticket_id=%s",
                ticket_id,
            )

    await message.answer(
        t("admin_support_reply_sent", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("support_staff_back_to_queue", language),
                        callback_data="ADM_SUPPORT",
                    )
                ]
            ]
        ),
    )
    await state.set_state(None)

async def update_support_ticket_status_from_admin(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    status: str,
):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_support_ticket_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    ticket_id = UUID(ids[index])

    try:
        async with get_session() as session:
            ticket = await SupportService(
                SupportRepository(session)
            ).update_ticket_status(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                ticket_id=ticket_id,
                status=status,
            )

            await ModerationRepository(session).log_admin_action(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                action_type=f"support_ticket_{status}",
                target_type="support_ticket",
                target_id=ticket_id,
                before_state={},
                after_state={"status": ticket.status},
                reason="support ticket status changed from Telegram admin panel",
            )
            await ModerationRepository(session).log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type=f"support_ticket_{status}",
                entity_type="support_ticket",
                entity_id=ticket_id,
                payload={"status": status},
            )
            if status == "resolved":
                await ModerationRepository(session).log_event(
                    tenant_id=tenant_id,
                    user_id=admin_user_id,
                    event_type="resolved",
                    entity_type="support_ticket",
                    entity_id=ticket_id,
                    payload={
                        "source": "support_staff",
                        "status": status,
                    },
                )
            await session.commit()
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    view = data.get("admin_support_view") or "in_progress"
    page = int(data.get("admin_support_page") or 0)

    await callback.message.answer(
        t("admin_support_status_updated", language).format(status=status),
        reply_markup=support_staff_ticket_actions_keyboard(
            view=view,
            page=page,
            has_next=False,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_SUP_ASSIGN:"))
async def assign_support_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_support_ticket_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    ticket_id = UUID(ids[index])

    try:
        async with get_session() as session:
            ticket = await SupportService(
                SupportRepository(session)
            ).assign_ticket(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                ticket_id=ticket_id,
            )

            await ModerationRepository(session).log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="ticket_assigned",
                entity_type="support_ticket",
                entity_id=ticket.id,
                payload={"status": ticket.status},
            )
            await session.commit()
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(t("support_staff_ticket_taken", language))
    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_SUP_ESCALATE:"))
async def ask_support_ticket_escalation_reason(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_support_ticket_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    if index < 0 or index >= len(ids):
        await callback.answer(t("admin_item_not_found", language), show_alert=True)
        return

    await state.update_data(
        admin_support_ticket_id=ids[index],
        admin_support_ticket_index=index,
    )
    await state.set_state(AdminModerationFSM.entering_support_escalation_reason)
    await callback.message.answer(t("admin_support_escalate_reason_prompt", language))
    await callback.answer()


@admin_router.message(AdminModerationFSM.entering_support_escalation_reason)
async def receive_support_ticket_escalation_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    language = normalize_language(message.from_user.language_code)

    ticket_id = data.get("admin_support_ticket_id")
    if not ticket_id:
        await message.answer(t("admin_item_not_found", language))
        await state.clear()
        return

    admin_user_id, tenant_id, roles = await get_admin_user_context(message.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_MENU_ROLES):
        await message.answer(t("admin_access_denied", language))
        await state.clear()
        return

    try:
        async with get_session() as session:
            ticket = await SupportService(
                SupportRepository(session)
            ).escalate_ticket_to_admin(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
                ticket_id=UUID(ticket_id),
                reason=message.text or "",
            )

            await ModerationRepository(session).log_event(
                tenant_id=tenant_id,
                user_id=admin_user_id,
                event_type="ticket_escalated",
                entity_type="support_ticket",
                entity_id=ticket.id,
                payload={"priority": ticket.priority},
            )
            await session.commit()
    except SupportServiceError as exc:
        await message.answer(t("support_error", language).format(error=str(exc)))
        return

    await state.clear()
    await message.answer(t("admin_support_escalated", language))

@admin_router.callback_query(F.data.startswith("ADM_SUP_RESOLVE:"))
async def resolve_support_ticket(callback: CallbackQuery, state: FSMContext):
    await update_support_ticket_status_from_admin(
        callback,
        state,
        status="resolved",
    )


@admin_router.callback_query(F.data.startswith("ADM_SUP_CLOSE:"))
async def close_support_ticket(callback: CallbackQuery, state: FSMContext):
    await update_support_ticket_status_from_admin(
        callback,
        state,
        status="closed",
    )

@admin_router.callback_query(F.data == "ADM_SUPPORT_STATS")
async def show_support_staff_stats(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)

    admin_user_id, tenant_id, roles = await get_admin_user_context(callback.from_user.id)
    if not admin_user_id or not tenant_id or not roles.intersection(ADMIN_SUPPORT_STATS_ROLES):
        await callback.answer(t("admin_access_denied", language), show_alert=True)
        return

    try:
        async with get_session() as session:
            stats = await SupportService(
                SupportRepository(session)
            ).get_staff_ticket_stats(
                tenant_id=tenant_id,
                staff_user_id=admin_user_id,
            )

            await EventRepository(session).create_event(
                event_type="stats_viewed",
                tenant_id=tenant_id,
                user_id=admin_user_id,
                entity_type="support_ticket",
                entity_id=None,
                payload={"source": "support_staff_stats"},
                platform="telegram",
            )
            await session.commit()
    except SupportServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.answer(
        format_support_staff_stats(stats, language),
        reply_markup=support_staff_stats_keyboard(language),
    )
    await callback.answer()


@admin_router.callback_query(F.data.in_({"ADM_SUPPORT_STATS_PERIOD", "ADM_SUPPORT_STATS_CATEGORY"}))
async def support_staff_stats_filter_pending(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)
    await callback.answer(
        t("support_staff_stats_filter_later", language),
        show_alert=True,
    )

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
    await send_global_main_menu(callback, state)

@admin_router.callback_query(F.data == "MAIN_MENU")
async def admin_main_menu_alias(
    callback: CallbackQuery,
    state: FSMContext,
):
    await send_global_main_menu(callback, state)

@admin_router.callback_query(
    (F.data == "ADM_ADMIN_SPECIALISTS")
    | F.data.startswith("ADM_ADMIN_SPECIALISTS:")
)
async def open_admin_specialists_list(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    status = "approved"
    page = 0

    if callback.data != "ADM_ADMIN_SPECIALISTS":
        parts = (callback.data or "").split(":")

        if len(parts) != 3:
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

        status = parts[1]

        try:
            page = max(int(parts[2]), 0)
        except (TypeError, ValueError):
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            specialist_page = await ModerationService(
                ModerationRepository(session)
            ).open_admin_specialists(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                status=status,
                page=page,
                page_size=ADMIN_SPECIALIST_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_specialist_ids=[
            str(item.specialist_id)
            for item in specialist_page.items
        ],
        admin_specialist_status=specialist_page.status,
        admin_specialist_page=specialist_page.page,
    )

    await callback.message.answer(
        t("admin_specialists_header", language).format(
            status=specialist_page.status,
            page=specialist_page.page + 1,
            count=len(specialist_page.items),
        )
    )

    if not specialist_page.items:
        await callback.message.answer(
            t("admin_specialists_empty", language),
            reply_markup=admin_specialists_keyboard(
                status=specialist_page.status,
                page=specialist_page.page,
                has_next=False,
                language=language,
            ),
        )
        await callback.answer()
        return

    for index, item in enumerate(specialist_page.items):
        number = (
            specialist_page.page
            * ADMIN_SPECIALIST_PAGE_SIZE
            + index
            + 1
        )

        await callback.message.answer(
            format_admin_specialist_item(
                item,
                number=number,
                language=language,
            ),
            reply_markup=admin_specialist_item_keyboard(
                index=index,
                language=language,
            ),
        )

    await callback.message.answer(
        t("admin_specialists_actions", language),
        reply_markup=admin_specialists_keyboard(
            status=specialist_page.status,
            page=specialist_page.page,
            has_next=specialist_page.has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_ADMIN_SPECIALIST_OPEN:")
)
async def open_admin_specialist_card(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    specialist_ids = data.get("admin_specialist_ids") or []
    status = (
        data.get("admin_specialist_status")
        or "approved"
    )
    page = int(
        data.get("admin_specialist_page") or 0
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    if index < 0 or index >= len(specialist_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    try:
        specialist_id = UUID(specialist_ids[index])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_moderator_specialist_card(
                moderator_user_id=admin_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    # Existing moderation handlers use this FSM key.
    await state.update_data(
        admin_pending_specialist_ids=specialist_ids,
        admin_pending_specialist_page=page,
    )

    await callback.message.answer(
        format_pending_specialist_card(
            card,
            language=language,
        ),
        reply_markup=admin_specialist_card_keyboard(
            index=index,
            status=card.status,
            page=page,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADMIN_SPECIALIST_READ_ONLY"
)
async def admin_specialist_read_only(
    callback: CallbackQuery,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await callback.answer(
        t("admin_specialist_read_only_notice", language),
        show_alert=True,
    )

@admin_router.callback_query(
    F.data == "ADM_ADMIN_SPECIALIST_FILTER"
)
async def open_admin_specialist_filter(
    callback: CallbackQuery,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection({"admin", "super_admin"})
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("admin_specialist_filter_title", language),
        reply_markup=admin_specialist_filter_keyboard(
            language
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    (F.data == "ADM_PENDING")
    | F.data.startswith("ADM_SP_QUEUE:")
)
async def list_pending_profiles(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    if callback.data == "ADM_PENDING":
        page = 0
    else:
        try:
            page = max(
                0,
                int((callback.data or "").split(":", 1)[1]),
            )
        except (TypeError, ValueError):
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

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
            items = await ModerationService(
                ModerationRepository(session)
            ).open_pending_specialists_queue(
                moderator_user_id=admin_user_id,
                tenant_id=tenant_id,
                page=page,
                page_size=MODERATOR_PROFILE_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    visible_items = items[:MODERATOR_PROFILE_PAGE_SIZE]
    has_next = len(items) > MODERATOR_PROFILE_PAGE_SIZE

    await state.update_data(
        admin_pending_specialist_ids=[
            str(item.specialist_id)
            for item in visible_items
        ],
        admin_pending_specialist_page=page,
    )

    await callback.message.answer(
        format_pending_profiles_header(
            page=page,
            count=len(visible_items),
            language=language,
        )
    )

    if not visible_items:
        await callback.message.answer(
            t("admin_no_pending_profiles", language),
            reply_markup=pending_profiles_queue_keyboard(
                page=page,
                has_next=False,
                language=language,
            ),
        )
        await callback.answer()
        return

    for index, item in enumerate(visible_items):
        number = page * MODERATOR_PROFILE_PAGE_SIZE + index + 1

        await callback.message.answer(
            format_pending_profile_queue_item(
                item,
                number=number,
                language=language,
            ),
            reply_markup=pending_profile_queue_item_keyboard(
                index=index,
                language=language,
            ),
        )

    await callback.message.answer(
        t("moderator_queue_actions", language),
        reply_markup=pending_profiles_queue_keyboard(
            page=page,
            has_next=has_next,
            language=language,
        ),
    )

    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_SP_OPEN:"))
async def open_pending_specialist(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()
    ids = data.get("admin_pending_specialist_ids") or []

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
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

    await show_pending_specialist(
        callback,
        state,
        index=index,
    )

async def show_pending_specialist(
    callback: CallbackQuery,
    state: FSMContext,
    index: int,
):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_pending_specialist_ids") or []
    page = int(data.get("admin_pending_specialist_page") or 0)

    if index < 0 or index >= len(ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

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
        specialist_id = UUID(ids[index])

        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_moderator_specialist_card(
                moderator_user_id=admin_user_id,
                tenant_id=tenant_id,
                specialist_id=specialist_id,
            )
    except (ValueError, ModerationError) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_pending_specialist_card(
            card,
            language=language,
        ),
        reply_markup=pending_specialist_keyboard(
            index=index,
            page=page,
            language=language,
        ),
    )

    await callback.answer()
@admin_router.callback_query(
    F.data.startswith("ADM_SP_APPROVE:")
    | F.data.startswith("ADM_SP_REJECT:")
)
async def ask_specialist_decision_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    try:
        callback_prefix, raw_index = (callback.data or "").split(":", 1)
        index = int(raw_index)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    specialist_ids = data.get("admin_pending_specialist_ids") or []
    page = int(data.get("admin_pending_specialist_page") or 0)

    if index < 0 or index >= len(specialist_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    decision = (
        "approved"
        if callback_prefix == "ADM_SP_APPROVE"
        else "rejected"
    )

    await state.update_data(
        moderator_decision_specialist_id=specialist_ids[index],
        moderator_decision=decision,
        moderator_decision_page=page,
    )
    await state.set_state(
        AdminModerationFSM.entering_specialist_decision_reason
    )

    await callback.message.answer(
        t("moderator_decision_reason_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_SP_DECISION_CANCEL:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_SP_HIDE:")
    | F.data.startswith("ADM_SP_RESTORE:")
)
async def ask_specialist_visibility_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    try:
        callback_prefix, raw_index = (
            callback.data or ""
        ).split(":", 1)
        index = int(raw_index)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    action_by_callback = {
        "ADM_SP_HIDE": "hide",
        "ADM_SP_RESTORE": "restore",
    }
    action = action_by_callback.get(callback_prefix)

    specialist_ids = data.get("admin_specialist_ids") or []
    status = (
        data.get("admin_specialist_status")
        or "approved"
    )
    page = int(data.get("admin_specialist_page") or 0)

    expected_status = (
        "approved"
        if action == "hide"
        else "hidden"
    )

    if (
        action is None
        or status != expected_status
        or index < 0
        or index >= len(specialist_ids)
    ):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
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

    await state.update_data(
        admin_specialist_visibility_action=action,
        admin_specialist_visibility_specialist_id=(
            specialist_ids[index]
        ),
        admin_specialist_visibility_status=status,
        admin_specialist_visibility_page=page,
    )
    await state.set_state(
        AdminModerationFSM.entering_specialist_visibility_reason
    )

    await callback.message.answer(
        t("moderator_decision_reason_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_VISIBILITY_CANCEL:"
                            f"{status}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM.entering_specialist_visibility_reason
)
async def receive_specialist_visibility_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(
            t("admin_reason_too_short", language)
        )
        return

    data = await state.get_data()
    action = data.get(
        "admin_specialist_visibility_action"
    )
    specialist_id = data.get(
        "admin_specialist_visibility_specialist_id"
    )
    status = data.get(
        "admin_specialist_visibility_status"
    )
    page = int(
        data.get("admin_specialist_visibility_page") or 0
    )

    expected_status = (
        "approved"
        if action == "hide"
        else "hidden"
    )

    if (
        not specialist_id
        or action not in {"hide", "restore"}
        or status != expected_status
    ):
        await state.clear()
        await message.answer(
            t("admin_item_not_found", language)
        )
        return

    confirmation_key = (
        "moderator_hide_specialist_confirmation"
        if action == "hide"
        else "moderator_restore_specialist_confirmation"
    )

    await state.update_data(
        admin_specialist_visibility_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_specialist_visibility
    )

    await message.answer(
        t(confirmation_key, language).format(
            reason=reason
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_decision_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_VISIBILITY_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_decision_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_VISIBILITY_EDIT"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_VISIBILITY_CANCEL:"
                            f"{status}:{page}"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_SP_VISIBILITY_EDIT"
)
async def edit_specialist_visibility_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    action = data.get(
        "admin_specialist_visibility_action"
    )
    specialist_id = data.get(
        "admin_specialist_visibility_specialist_id"
    )

    if (
        not specialist_id
        or action not in {"hide", "restore"}
    ):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_specialist_visibility_reason
    )
    await callback.message.answer(
        t("moderator_decision_reason_prompt", language)
    )
    await callback.answer()


@admin_router.callback_query(
    F.data.startswith("ADM_SP_VISIBILITY_CANCEL:")
)
async def cancel_specialist_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        _, status, raw_page = (
            callback.data or ""
        ).split(":", 2)
        page = max(int(raw_page), 0)
    except (TypeError, ValueError):
        status = "approved"
        page = 0

    if status not in {"approved", "hidden"}:
        status = "approved"

    await state.clear()

    await callback.message.answer(
        t("moderator_decision_cancelled", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_panel_back",
                            language,
                        ),
                        callback_data=(
                            f"ADM_ADMIN_SPECIALISTS:{status}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_SP_VISIBILITY_CONFIRM"
)
async def confirm_specialist_visibility(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    action = data.get(
        "admin_specialist_visibility_action"
    )
    specialist_id = data.get(
        "admin_specialist_visibility_specialist_id"
    )
    reason = (
        data.get("admin_specialist_visibility_reason") or ""
    ).strip()

    if (
        not specialist_id
        or action not in {"hide", "restore"}
        or len(reason) < 3
    ):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    admin_user_id, tenant_id, roles = (
        await get_admin_user_context(callback.from_user.id)
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            service = ModerationService(
                ModerationRepository(session)
            )

            if action == "hide":
                result = await service.hide_specialist(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    specialist_id=UUID(specialist_id),
                    reason=reason,
                )
            else:
                result = await service.restore_specialist(
                    admin_user_id=admin_user_id,
                    tenant_id=tenant_id,
                    specialist_id=UUID(specialist_id),
                    reason=reason,
                )
    except (ModerationError, ValueError) as exc:
        logger.warning(
            "specialist_visibility_change_failed "
            "telegram_id=%s specialist_id=%s action=%s error=%s",
            callback.from_user.id,
            specialist_id,
            action,
            exc,
        )
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    result_text_key = (
        "moderator_specialist_hidden"
        if action == "hide"
        else "moderator_specialist_restored"
    )

    logger.info(
        "specialist_visibility_change_completed "
        "telegram_id=%s admin_user_id=%s "
        "specialist_id=%s action=%s status=%s",
        callback.from_user.id,
        admin_user_id,
        specialist_id,
        action,
        result.status,
    )

    await state.clear()

    await callback.message.answer(
        t(result_text_key, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "admin_panel_back",
                            language,
                        ),
                        callback_data=(
                            "ADM_ADMIN_SPECIALISTS:"
                            f"{result.status}:0"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM.entering_specialist_decision_reason
)
async def receive_specialist_decision_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    data = await state.get_data()
    specialist_id = data.get("moderator_decision_specialist_id")
    decision = data.get("moderator_decision")
    page = int(data.get("moderator_decision_page") or 0)

    if not specialist_id or decision not in {"approved", "rejected"}:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    await state.update_data(
        moderator_decision_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_specialist_decision
    )

    confirmation_key = (
        "moderator_approve_confirmation"
        if decision == "approved"
        else "moderator_reject_confirmation"
    )

    await message.answer(
        t(confirmation_key, language).format(reason=reason),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_decision_confirm_btn",
                            language,
                        ),
                        callback_data="ADM_SP_DECISION_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_decision_edit_btn",
                            language,
                        ),
                        callback_data="ADM_SP_DECISION_EDIT",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_SP_DECISION_CANCEL:{page}"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_SP_DECISION_EDIT"
)
async def edit_specialist_decision_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    specialist_id = data.get("moderator_decision_specialist_id")
    decision = data.get("moderator_decision")

    if not specialist_id or decision not in {"approved", "rejected"}:
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_specialist_decision_reason
    )
    await callback.message.answer(
        t("moderator_decision_reason_prompt", language)
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_SP_DECISION_CANCEL:")
)
async def cancel_specialist_decision(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        page = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (TypeError, ValueError):
        page = 0

    await state.clear()

    await callback.message.answer(
        t("moderator_decision_cancelled", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_QUEUE:{page}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_SP_DECISION_CONFIRM"
)
async def confirm_specialist_decision(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    specialist_id = data.get("moderator_decision_specialist_id")
    decision = data.get("moderator_decision")
    reason = (data.get("moderator_decision_reason") or "").strip()
    page = int(data.get("moderator_decision_page") or 0)

    if (
        not specialist_id
        or decision not in {"approved", "rejected"}
        or len(reason) < 3
    ):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            service = ModerationService(
                ModerationRepository(session)
            )

            if decision == "approved":
                result = await service.approve_specialist(
                    admin_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    specialist_id=UUID(specialist_id),
                    reason=reason,
                )
            else:
                result = await service.reject_specialist(
                    admin_user_id=moderator_user_id,
                    tenant_id=tenant_id,
                    specialist_id=UUID(specialist_id),
                    reason=reason,
                )

    except (ModerationError, ValueError) as exc:
        logger.warning(
            "moderator_specialist_decision_failed "
            "telegram_id=%s specialist_id=%s "
            "decision=%s error=%s",
            callback.from_user.id,
            specialist_id,
            decision,
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return

    logger.info(
        "moderator_specialist_decision_completed "
        "telegram_id=%s moderator_user_id=%s "
        "specialist_id=%s decision=%s status=%s",
        callback.from_user.id,
        moderator_user_id,
        specialist_id,
        decision,
        result.status,
    )

    result_text_key = (
        "moderator_decision_approved"
        if decision == "approved"
        else "moderator_decision_rejected"
    )

    await state.clear()

    await callback.message.answer(
        t(result_text_key, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_QUEUE:{page}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_SP_CHANGES:"))
async def ask_specialist_changes_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    specialist_ids = data.get("admin_pending_specialist_ids") or []
    page = int(data.get("admin_pending_specialist_page") or 0)

    if index < 0 or index >= len(specialist_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_changes_specialist_id=specialist_ids[index],
        moderator_changes_page=page,
    )
    await state.set_state(
        AdminModerationFSM.entering_specialist_changes_reason
    )

    await callback.message.answer(
        t("moderator_changes_reason_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("moderator_changes_cancel_btn", language),
                        callback_data=f"ADM_SP_QUEUE:{page}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM.entering_specialist_changes_reason
)
async def receive_specialist_changes_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    data = await state.get_data()
    specialist_id = data.get("moderator_changes_specialist_id")
    page = int(data.get("moderator_changes_page") or 0)

    if not specialist_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    await state.update_data(
        moderator_changes_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_specialist_changes
    )

    await message.answer(
        t("moderator_changes_confirmation", language).format(
            reason=reason,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_confirm_btn",
                            language,
                        ),
                        callback_data="ADM_SP_CHANGES_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_edit_btn",
                            language,
                        ),
                        callback_data="ADM_SP_CHANGES_EDIT",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_CHANGES_CANCEL:{page}",
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_SP_CHANGES_EDIT"
)
async def edit_specialist_changes_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    if not data.get("moderator_changes_specialist_id"):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_specialist_changes_reason
    )
    await callback.message.answer(
        t("moderator_changes_reason_prompt", language)
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_SP_CHANGES_CANCEL:")
)
async def cancel_specialist_changes(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        page = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (TypeError, ValueError):
        page = 0

    await state.clear()

    await callback.message.answer(
        t("moderator_changes_cancelled", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_QUEUE:{page}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_SP_CHANGES_CONFIRM"
)
async def confirm_specialist_changes(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    specialist_id = data.get("moderator_changes_specialist_id")
    reason = (data.get("moderator_changes_reason") or "").strip()
    page = int(data.get("moderator_changes_page") or 0)

    if not specialist_id or len(reason) < 3:
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).request_specialist_changes(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                specialist_id=UUID(specialist_id),
                reason=reason,
            )
    except (ModerationError, ValueError) as exc:
        logger.warning(
            "moderator_specialist_changes_failed "
            "telegram_id=%s specialist_id=%s error=%s",
            callback.from_user.id,
            specialist_id,
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return

    logger.info(
        "moderator_specialist_changes_requested "
        "telegram_id=%s moderator_user_id=%s "
        "specialist_id=%s status=%s",
        callback.from_user.id,
        moderator_user_id,
        specialist_id,
        result.status,
    )

    await state.clear()

    await callback.message.answer(
        t("moderator_changes_submitted", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_QUEUE:{page}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_SP_SCOPED_BLOCK:")
)
async def ask_specialist_scoped_block_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    specialist_ids = data.get("admin_pending_specialist_ids") or []
    page = int(data.get("admin_pending_specialist_page") or 0)

    if index < 0 or index >= len(specialist_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_scoped_block_specialist_id=specialist_ids[index],
        moderator_scoped_block_page=page,
    )
    await state.set_state(
        AdminModerationFSM.entering_specialist_scoped_block_reason
    )

    await callback.message.answer(
        t("moderator_scoped_block_reason_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_SP_SCOPED_BLOCK_CANCEL:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM.entering_specialist_scoped_block_reason
)
async def receive_specialist_scoped_block_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    data = await state.get_data()
    specialist_id = data.get(
        "moderator_scoped_block_specialist_id"
    )
    page = int(data.get("moderator_scoped_block_page") or 0)

    if not specialist_id:
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    await state.update_data(
        moderator_scoped_block_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_specialist_scoped_block
    )

    await message.answer(
        t(
            "moderator_scoped_block_confirmation",
            language,
        ).format(reason=reason),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_SCOPED_BLOCK_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_SP_SCOPED_BLOCK_EDIT"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_SP_SCOPED_BLOCK_CANCEL:{page}"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_SP_SCOPED_BLOCK_EDIT"
)
async def edit_specialist_scoped_block_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    if not data.get("moderator_scoped_block_specialist_id"):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_specialist_scoped_block_reason
    )
    await callback.message.answer(
        t("moderator_scoped_block_reason_prompt", language)
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_SP_SCOPED_BLOCK_CANCEL:")
)
async def cancel_specialist_scoped_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        page = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (TypeError, ValueError):
        page = 0

    await state.clear()

    await callback.message.answer(
        t("moderator_scoped_block_cancelled", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_QUEUE:{page}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_SP_SCOPED_BLOCK_CONFIRM"
)
async def confirm_specialist_scoped_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    specialist_id = data.get(
        "moderator_scoped_block_specialist_id"
    )
    reason = (
        data.get("moderator_scoped_block_reason") or ""
    ).strip()
    page = int(data.get("moderator_scoped_block_page") or 0)

    if not specialist_id or len(reason) < 3:
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).add_specialist_owner_scoped_blacklist(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                specialist_id=UUID(specialist_id),
                reason=reason,
            )
    except (ModerationError, ValueError) as exc:
        logger.warning(
            "moderator_scoped_blacklist_failed "
            "telegram_id=%s specialist_id=%s error=%s",
            callback.from_user.id,
            specialist_id,
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return

    logger.info(
        "moderator_scoped_blacklist_created "
        "telegram_id=%s moderator_user_id=%s "
        "specialist_id=%s blacklist_id=%s",
        callback.from_user.id,
        moderator_user_id,
        specialist_id,
        result.entity_id,
    )

    await state.clear()

    await callback.message.answer(
        t("moderator_scoped_block_created", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data=f"ADM_SP_QUEUE:{page}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_GLOBAL_BLACKLIST"
)
async def open_active_global_blacklist(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_global_blacklist_queue(
        callback,
        state,
        view="active",
        page=0,
    )


@admin_router.callback_query(
    F.data.startswith("ADM_GBL_QUEUE:")
)
async def change_global_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    if len(parts) != 3:
        await callback.answer()
        return

    view = parts[1]

    if view not in {"active", "history"}:
        view = "active"

    try:
        page = max(0, int(parts[2]))
    except ValueError:
        page = 0

    await open_global_blacklist_queue(
        callback,
        state,
        view=view,
        page=page,
    )


async def open_global_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    (
        admin_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not admin_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_GLOBAL_BLACKLIST_ROLES
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).open_global_blacklist_queue(
                admin_user_id=admin_user_id,
                tenant_id=tenant_id,
                view=view,
                page=page,
                page_size=ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE,
            )
    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.update_data(
        admin_global_blacklist_ids=[
            str(card.blacklist_id)
            for card in result.items
        ],
        admin_user_search_ids=[
            str(card.user_id)
            for card in result.items
        ],
        admin_global_blacklist_user_ids=[
            str(card.user_id)
            for card in result.items
        ],
        admin_global_blacklist_can_revoke=[
            card.can_revoke
            for card in result.items
        ],
        admin_global_blacklist_view=result.view,
        admin_global_blacklist_page=result.page,
    )

    view_label = t(
        (
            "admin_global_blacklist_history_title"
            if result.view == "history"
            else "admin_global_blacklist_active_title"
        ),
        language,
    )

    await callback.message.answer(
        t(
            "admin_global_blacklist_queue_title",
            language,
        ).format(
            view=view_label,
            count=len(result.items),
        )
    )

    if not result.items:
        await callback.message.answer(
            t("admin_global_blacklist_empty", language),
            reply_markup=global_blacklist_queue_keyboard(
                view=result.view,
                page=result.page,
                has_next=False,
                language=language,
            ),
        )
        await callback.answer()
        return

    start_number = (
        result.page * ADMIN_GLOBAL_BLACKLIST_PAGE_SIZE
        + 1
    )

    for offset, card in enumerate(result.items):
        await callback.message.answer(
            format_global_blacklist_card(
                card,
                number=start_number + offset,
                language=language,
            ),
            reply_markup=global_blacklist_card_keyboard(
                index=offset,
                can_revoke=card.can_revoke,
                language=language,
            ),
        )

    await callback.message.answer(
        t(
            "admin_global_blacklist_actions_title",
            language,
        ),
        reply_markup=global_blacklist_queue_keyboard(
            view=result.view,
            page=result.page,
            has_next=result.has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_SCOPED_BLACKLIST"
)
async def open_active_scoped_blacklist(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_scoped_blacklist_queue(
        callback,
        state,
        view="active",
        page=0,
    )


@admin_router.callback_query(
    F.data.startswith("ADM_BL_QUEUE:")
)
async def change_scoped_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    view = (
        parts[1]
        if len(parts) > 1
        and parts[1] in {"active", "revoked"}
        else "active"
    )

    try:
        page = max(
            0,
            int(parts[2]),
        )
    except (IndexError, TypeError, ValueError):
        page = 0

    await open_scoped_blacklist_queue(
        callback,
        state,
        view=view,
        page=page,
    )

async def open_scoped_blacklist_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    (
        moderator_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            cards = await ModerationService(
                ModerationRepository(session)
            ).open_scoped_blacklist_queue(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                view=view,
                page=page,
                page_size=5,
            )

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    has_next = len(cards) > 5
    visible_cards = cards[:5]
    state_data = await state.get_data()

    await state.update_data(
        moderator_blacklist_ids=[
            str(card.blacklist_id)
            for card in visible_cards
        ],
        moderator_blacklist_can_revoke=[
            card.can_revoke
            for card in visible_cards
        ],
        moderator_blacklist_view=view,
        moderator_blacklist_page=page,
        moderator_blacklist_has_next=has_next,
    )

    view_label = t(
        (
            "moderator_blacklist_history_title"
            if view == "revoked"
            else "moderator_blacklist_active_title"
        ),
        language,
    )

    await callback.answer()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=[
            state_data.get(
                "last_menu_message_id"
            ),
            *(
                state_data.get(
                    "admin_scoped_blacklist_message_ids"
                )
                or []
            ),
        ],
    )

    rendered_message_ids: list[int] = []

    header_message = await callback.message.answer(
        t(
            "moderator_blacklist_queue_title",
            language,
        ).format(
            view=view_label,
            count=len(visible_cards),
        )
    )
    rendered_message_ids.append(
        header_message.message_id
    )

    if not visible_cards:
        empty_message = await callback.message.answer(
            t(
                "moderator_blacklist_empty",
                language,
            ),
            reply_markup=scoped_blacklist_queue_keyboard(
                view=view,
                page=page,
                has_next=False,
                language=language,
            ),
        )
        rendered_message_ids.append(
            empty_message.message_id
        )

        await state.update_data(
            admin_scoped_blacklist_message_ids=(
                rendered_message_ids
            ),
            last_menu_message_id=None,
        )
        return

    start_number = page * 5 + 1

    for offset, card in enumerate(visible_cards):
        card_message = await callback.message.answer(
            format_scoped_blacklist_card(
                card,
                number=start_number + offset,
                language=language,
            ),
            reply_markup=scoped_blacklist_card_keyboard(
                index=offset,
                can_revoke=card.can_revoke,
                language=language,
            ),
        )
        rendered_message_ids.append(
            card_message.message_id
        )

    navigation_message = await callback.message.answer(
        t(
            "moderator_blacklist_actions_title",
            language,
        ),
        reply_markup=scoped_blacklist_queue_keyboard(
            view=view,
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    rendered_message_ids.append(
        navigation_message.message_id
    )

    await state.update_data(
        admin_scoped_blacklist_message_ids=(
            rendered_message_ids
        ),
        last_menu_message_id=None,
    )

@admin_router.callback_query(
    F.data.startswith("ADM_BL_REVOKE:")
)
async def ask_scoped_blacklist_revoke_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (TypeError, ValueError):
        index = -1

    blacklist_ids = (
        data.get("moderator_blacklist_ids")
        or []
    )
    revoke_flags = (
        data.get("moderator_blacklist_can_revoke")
        or []
    )

    if (
        index < 0
        or index >= len(blacklist_ids)
        or index >= len(revoke_flags)
        or not revoke_flags[index]
    ):
        await callback.answer(
            t(
                "moderator_blacklist_revoke_forbidden",
                language,
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_blacklist_revoke_id=(
            blacklist_ids[index]
        ),
    )
    await state.set_state(
        AdminModerationFSM
        .entering_blacklist_revoke_reason
    )

    await callback.answer()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_ids=(
            data.get(
                "admin_scoped_blacklist_message_ids"
            )
            or []
        ),
    )

    await state.update_data(
        admin_scoped_blacklist_message_ids=[],
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_revoke_reason_prompt",
            language,
        ),
        callback_answered=True,
    )

@admin_router.message(
    AdminModerationFSM.entering_blacklist_revoke_reason
)
async def receive_scoped_blacklist_revoke_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

    if len(reason) < 3:
        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_reason_too_short",
                language,
            ),
        )
        return

    if not data.get(
        "moderator_blacklist_revoke_id"
    ):
        await state.clear()

        await replace_admin_input_screen(
            message=message,
            state=state,
            text=t(
                "admin_item_not_found",
                language,
            ),
        )
        return

    await state.update_data(
        moderator_blacklist_revoke_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_blacklist_revoke
    )

    await replace_admin_input_screen(
        message=message,
        state=state,
        text=t(
            "moderator_blacklist_revoke_confirmation",
            language,
        ).format(
            reason=reason
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_revoke_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_REVOKE_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_edit_btn",
                            language,
                        ),
                        callback_data="ADM_BL_REVOKE_EDIT",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_REVOKE_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_BL_REVOKE_EDIT"
)
async def edit_scoped_blacklist_revoke_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get("moderator_blacklist_revoke_id"):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .entering_blacklist_revoke_reason
    )
    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_revoke_reason_prompt",
            language,
        ),
    )


@admin_router.callback_query(
    F.data == "ADM_BL_REVOKE_CANCEL"
)
async def cancel_scoped_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    view = (
        data.get("moderator_blacklist_view")
        or "active"
    )
    page = int(
        data.get("moderator_blacklist_page")
        or 0
    )

    await state.clear()

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_revoke_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_BL_QUEUE:{view}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_BL_REVOKE_CONFIRM"
)
async def confirm_scoped_blacklist_revoke(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    blacklist_id = data.get(
        "moderator_blacklist_revoke_id"
    )
    reason = (
        data.get("moderator_blacklist_revoke_reason")
        or ""
    ).strip()
    view = (
        data.get("moderator_blacklist_view")
        or "active"
    )
    page = int(
        data.get("moderator_blacklist_page")
        or 0
    )

    if not blacklist_id or len(reason) < 3:
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    (
        moderator_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).revoke_scoped_blacklist(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                blacklist_id=UUID(blacklist_id),
                reason=reason,
            )

    except (ModerationError, ValueError) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "scoped_blacklist_revoked "
        "telegram_id=%s blacklist_id=%s status=%s",
        callback.from_user.id,
        blacklist_id,
        result.status,
    )

    await state.clear()

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "moderator_blacklist_revoked",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_active_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_QUEUE:active:0"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_blacklist_history_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_BL_QUEUE:revoked:0"
                        ),
                    )
                ],
            ]
        ),
    )


@admin_router.callback_query(F.data == "ADM_COMPLAINTS")
async def list_open_complaints(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_complaints_queue(
        callback,
        state,
        view="open",
        page=0,
    )


@admin_router.callback_query(F.data.startswith("ADM_CP_QUEUE:"))
async def change_complaints_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    parts = (callback.data or "").split(":")

    view = (
        parts[1]
        if len(parts) > 1
        else "open"
    )

    try:
        page = int(parts[2])
    except (IndexError, TypeError, ValueError):
        page = 0

    await open_complaints_queue(
        callback,
        state,
        view=view,
        page=max(page, 0),
    )


@admin_router.callback_query(F.data == "ADM_CP_FILTER")
async def show_complaints_filter(
    callback: CallbackQuery,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await callback.message.answer(
        t("moderator_complaint_filter_title", language),
        reply_markup=complaints_filter_keyboard(language),
    )
    await callback.answer()

async def open_complaints_queue(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    view: str,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    statuses_by_view = {
        "open": {"new", "in_review"},
        "new": {"new"},
        "in_review": {"in_review"},
        "resolved": {"resolved"},
        "rejected": {"rejected"},
    }
    statuses = statuses_by_view.get(
        view,
        {"new", "in_review"},
    )
    normalized_view = (
        view
        if view in statuses_by_view
        else "open"
    )

    try:
        async with get_session() as session:
            results = await ModerationService(
                ModerationRepository(session)
            ).open_complaints_queue(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                statuses=statuses,
                page=page,
                page_size=5,
            )

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    cards = results[:5]
    has_next = len(results) > 5

    await state.update_data(
        admin_complaint_ids=[
            str(card.complaint_id)
            for card in cards
        ],
        admin_complaint_view=normalized_view,
        admin_complaint_page=page,
        admin_complaint_has_next=has_next,
    )

    if not cards:
        await callback.message.answer(
            t("admin_no_open_complaints", language),
        )
        await show_admin_panel(
            callback,
            state,
        )
        return

    await callback.message.answer(
        t(
            "moderator_complaint_queue_title",
            language,
        ).format(
            count=len(cards),
        )
    )

    start_number = page * 5 + 1

    for offset, card in enumerate(cards):
        await callback.message.answer(
            format_complaint_queue_item(
                card,
                number=start_number + offset,
                language=language,
            ),
            reply_markup=complaint_queue_item_keyboard(
                index=offset,
                can_take=(
                    card.status == "new"
                    and not card.requires_admin_escalation
                ),
                language=language,
            ),
        )

    await callback.message.answer(
        t("moderator_queue_actions", language),
        reply_markup=complaints_queue_keyboard(
            view=normalized_view,
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data.startswith("ADM_CP_TAKE:"))
async def take_complaint_from_queue(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        index = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        index = -1

    complaint_ids = data.get("admin_complaint_ids") or []
    view = data.get("admin_complaint_view") or "open"
    page = int(data.get("admin_complaint_page") or 0)

    if index < 0 or index >= len(complaint_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = (
        await get_admin_user_context(
            callback.from_user.id
        )
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            await ModerationService(
                ModerationRepository(session)
            ).take_complaint(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                complaint_id=UUID(
                    complaint_ids[index]
                ),
            )

    except ModerationError:
        await callback.answer(
            t(
                "moderator_complaint_take_unavailable",
                language,
            ),
            show_alert=True,
        )
        return

    await callback.message.answer(
        t("moderator_complaint_taken", language),
    )

    await open_complaints_queue(
        callback,
        state,
        view=view,
        page=page,
    )

async def show_complaint(
    callback: CallbackQuery,
    state: FSMContext,
    index: int,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )
    ids = data.get("admin_complaint_ids") or []

    view = (
        data.get("admin_complaint_view")
        or "open"
    )
    page = int(
        data.get("admin_complaint_page")
        or 0
    )

    if not ids:
        await callback.message.answer(
            t("admin_no_open_complaints", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    index = max(
        0,
        min(int(index), len(ids) - 1),
    )

    (
        moderator_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await ModerationService(
                ModerationRepository(session)
            ).get_moderator_complaint_card(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                complaint_id=UUID(ids[index]),
            )

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_complaint_card(
            card,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=complaint_keyboard(
            index=index,
            total=len(ids),
            status=card.status,
            requires_admin_escalation=(
                card.requires_admin_escalation
            ),
            view=view,
            page=page,
            language=language,
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_REVIEWS")
async def list_pending_reviews(
    callback: CallbackQuery,
    state: FSMContext,
):
    await open_pending_reviews_page(
        callback,
        state,
        page=0,
    )


@admin_router.callback_query(F.data.startswith("ADM_REVIEWS_PAGE:"))
async def change_pending_reviews_page(
    callback: CallbackQuery,
    state: FSMContext,
):
    try:
        page = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        page = 0

    await open_pending_reviews_page(
        callback,
        state,
        page=max(page, 0),
    )


async def open_pending_reviews_page(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    page: int,
):
    language = normalize_language(
        callback.from_user.language_code
    )

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
            results = await ReviewService(
                ReviewRepository(session)
            ).list_pending_reviews(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                page=page,
                page_size=5,
            )

    except ReviewServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    reviews = results[:5]
    has_next = len(results) > 5

    if not reviews:
        await state.update_data(
            admin_review_ids=[],
            admin_review_page=page,
            admin_review_has_next=False,
        )

        await callback.message.answer(
            t("admin_no_pending_reviews", language),
        )
        await show_admin_panel(
            callback,
            state,
        )
        return

    await state.update_data(
        admin_review_ids=[
            str(review.id)
            for review in reviews
        ],
        admin_review_page=page,
        admin_review_has_next=has_next,
    )

    await show_review(
        callback,
        state,
        index=0,
    )
async def show_review(callback: CallbackQuery, state: FSMContext, index: int):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    ids = data.get("admin_review_ids") or []
    page = int(data.get("admin_review_page") or 0)
    has_next = bool(data.get("admin_review_has_next"))
    if not ids:
        await callback.message.answer(
            t("admin_no_pending_reviews", language),
        )
        await show_admin_panel(
            callback,
            state,
        )
        return

    index = max(0, min(int(index), len(ids) - 1))

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
            card = await ReviewService(
                ReviewRepository(session)
            ).get_pending_review_for_moderation(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                review_id=UUID(ids[index]),
            )

    except ReviewServiceError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return
    
    await callback.message.answer(
        format_review_card(
            card,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=review_keyboard(
            index=index,
            total=len(ids),
            page=page,
            has_next=has_next,
            language=language,
        ),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("ADM_RV_VIEW:"))
async def view_pending_review(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_review(callback, state, index=index)


@admin_router.callback_query(F.data.startswith("ADM_RV_APPROVE:"))
async def approve_pending_review(
    callback: CallbackQuery,
    state: FSMContext,
):
    data = await state.get_data()
    language = normalize_language(callback.from_user.language_code)
    index = int(callback.data.split(":", 1)[1])
    ids = data.get("admin_review_ids") or []

    if index < 0 or index >= len(ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

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
            result = await ReviewService(
                ReviewRepository(session)
            ).moderate_review(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                review_id=UUID(ids[index]),
                status="published",
                reason="shown by moderator",
            )

    except ReviewServiceError as exc:
        await callback.answer(
            review_moderation_error_text(
                exc,
                language,
            ),
            show_alert=True,
        )
        return

    await callback.message.edit_reply_markup(
        reply_markup=None,
    )

    await callback.message.answer(
        t("admin_review_updated", language).format(
            status=t(
                "admin_review_status_published",
                language,
            ),
        ),
    )

    await show_admin_panel(
        callback,
        state,
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
        admin_review_source_chat_id=callback.message.chat.id,
        admin_review_source_message_id=callback.message.message_id,
    )
    await state.set_state(state_name)
    await callback.message.answer(t("admin_reason_prompt", language))
    await callback.answer()


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

    if status != "hidden" or not review_id:
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
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                review_id=review_uuid,
                status="hidden",
                reason=reason,
            )

    except ReviewServiceError as exc:

        source_chat_id = data.get(
            "admin_review_source_chat_id"
        )
        source_message_id = data.get(
            "admin_review_source_message_id"
        )

        if source_chat_id and source_message_id:
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=source_chat_id,
                    message_id=source_message_id,
                    reply_markup=None,
                )
            except Exception:
                logger.info(
                    "review_moderation_markup_already_removed "
                    "message_id=%s",
                    source_message_id,
                )

        await state.clear()

        await message.answer(
            review_moderation_error_text(
                exc,
                language,
            )
        )

        await show_admin_panel(
            message,
            state,
        )
        return

    await state.clear()

    await message.answer(
        t("admin_review_updated", language).format(
            status=t(
                "admin_review_status_hidden",
                language,
            ),
        )
    )

    await show_admin_panel(
        message,
        state,
    )

@admin_router.callback_query(F.data.startswith("ADM_CP_VIEW:"))
async def view_complaint(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_complaint(callback, state, index=index)

@admin_router.callback_query(F.data.startswith("ADM_CP_REVIEW:"))
async def ask_review_complaint_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    await prepare_complaint_resolution(
        callback,
        state,
        status="in_review",
    )

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
    if (
        not admin_user_id
        or not tenant_id
        or not roles
        or not complaint_id
    ):
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
                tenant_id=tenant_id,
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

@admin_router.callback_query(
    F.data.startswith("ADM_CP_SCOPED_BLOCK:")
)
async def ask_complaint_scoped_block_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (TypeError, ValueError):
        index = -1

    complaint_ids = (
        data.get("admin_complaint_ids")
        or []
    )
    view = (
        data.get("admin_complaint_view")
        or "open"
    )
    page = int(
        data.get("admin_complaint_page")
        or 0
    )

    if index < 0 or index >= len(complaint_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_complaint_scoped_id=(
            complaint_ids[index]
        ),
        moderator_complaint_scoped_index=index,
        moderator_complaint_scoped_view=view,
        moderator_complaint_scoped_page=page,
    )
    await state.set_state(
        AdminModerationFSM
        .entering_complaint_scoped_block_reason
    )

    await callback.message.answer(
        t(
            "moderator_scoped_block_reason_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_SCOPED_BLOCK_CANCEL"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM
    .entering_complaint_scoped_block_reason
)
async def receive_complaint_scoped_block_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(
            t("admin_reason_too_short", language)
        )
        return

    data = await state.get_data()

    if not data.get("moderator_complaint_scoped_id"):
        await state.clear()
        await message.answer(
            t("admin_item_not_found", language)
        )
        return

    await state.update_data(
        moderator_complaint_scoped_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM
        .confirming_complaint_scoped_block
    )

    await message.answer(
        t(
            "moderator_scoped_block_confirmation",
            language,
        ).format(reason=reason),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_confirm_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_SCOPED_BLOCK_CONFIRM"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_edit_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_SCOPED_BLOCK_EDIT"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_SCOPED_BLOCK_CANCEL"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    F.data.startswith("ADM_CP_ADMIN:")
)
async def ask_complaint_admin_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    try:
        index = int(
            (callback.data or "").split(":", 1)[1]
        )
    except (TypeError, ValueError):
        index = -1

    complaint_ids = (
        data.get("admin_complaint_ids")
        or []
    )
    view = (
        data.get("admin_complaint_view")
        or "open"
    )
    page = int(
        data.get("admin_complaint_page")
        or 0
    )

    if index < 0 or index >= len(complaint_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.update_data(
        moderator_complaint_admin_id=(
            complaint_ids[index]
        ),
        moderator_complaint_admin_view=view,
        moderator_complaint_admin_page=page,
    )
    await state.set_state(
        AdminModerationFSM
        .entering_complaint_admin_reason
    )

    await callback.message.answer(
        t(
            "moderator_complaint_admin_reason_prompt",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            "ADM_CP_ADMIN_CANCEL"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM.entering_complaint_admin_reason
)
async def receive_complaint_admin_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(
        message.from_user.language_code
    )
    reason = (message.text or "").strip()
    data = await state.get_data()

    if len(reason) < 3:
        await message.answer(
            t("admin_reason_too_short", language)
        )
        return

    if not data.get("moderator_complaint_admin_id"):
        await state.clear()
        await message.answer(
            t("admin_item_not_found", language)
        )
        return

    await state.update_data(
        moderator_complaint_admin_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_complaint_admin
    )

    await message.answer(
        t(
            "moderator_complaint_admin_confirmation",
            language,
        ).format(reason=reason),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_complaint_admin_confirm_btn",
                            language,
                        ),
                        callback_data="ADM_CP_ADMIN_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_scoped_block_edit_btn",
                            language,
                        ),
                        callback_data="ADM_CP_ADMIN_EDIT",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data="ADM_CP_ADMIN_CANCEL",
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_CP_ADMIN_EDIT"
)
async def edit_complaint_admin_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get("moderator_complaint_admin_id"):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .entering_complaint_admin_reason
    )
    await callback.message.answer(
        t(
            "moderator_complaint_admin_reason_prompt",
            language,
        )
    )
    await callback.answer()


@admin_router.callback_query(
    F.data == "ADM_CP_ADMIN_CANCEL"
)
async def cancel_complaint_admin_escalation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    view = (
        data.get("moderator_complaint_admin_view")
        or "open"
    )
    page = int(
        data.get("moderator_complaint_admin_page")
        or 0
    )

    await state.clear()

    await callback.message.answer(
        t(
            "moderator_complaint_admin_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_complaint_back_queue_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_CP_QUEUE:{view}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_CP_SCOPED_BLOCK_EDIT"
)
async def edit_complaint_scoped_block_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get("moderator_complaint_scoped_id"):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .entering_complaint_scoped_block_reason
    )
    await callback.message.answer(
        t(
            "moderator_scoped_block_reason_prompt",
            language,
        )
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_CP_SCOPED_BLOCK_CANCEL"
)
async def cancel_complaint_scoped_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    view = (
        data.get("moderator_complaint_scoped_view")
        or "open"
    )
    page = int(
        data.get("moderator_complaint_scoped_page")
        or 0
    )

    await state.clear()

    await callback.message.answer(
        t(
            "moderator_scoped_block_cancelled",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_complaint_back_queue_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_CP_QUEUE:{view}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_CP_ADMIN_CONFIRM"
)
async def confirm_complaint_admin_escalation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    complaint_id = data.get(
        "moderator_complaint_admin_id"
    )
    reason = (
        data.get("moderator_complaint_admin_reason")
        or ""
    ).strip()
    view = (
        data.get("moderator_complaint_admin_view")
        or "open"
    )
    page = int(
        data.get("moderator_complaint_admin_page")
        or 0
    )

    if not complaint_id or len(reason) < 3:
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    (
        moderator_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).escalate_complaint_to_admin(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                complaint_id=UUID(complaint_id),
                reason=reason,
            )

    except (ModerationError, ValueError) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "complaint_escalated_to_admin "
        "telegram_id=%s complaint_id=%s status=%s",
        callback.from_user.id,
        complaint_id,
        result.status,
    )

    await state.clear()

    await callback.message.answer(
        t(
            "moderator_complaint_admin_completed",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_complaint_back_queue_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_CP_QUEUE:{view}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_CP_SCOPED_BLOCK_CONFIRM"
)
async def confirm_complaint_scoped_block(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    complaint_id = data.get(
        "moderator_complaint_scoped_id"
    )
    reason = (
        data.get(
            "moderator_complaint_scoped_reason"
        )
        or ""
    ).strip()
    view = (
        data.get("moderator_complaint_scoped_view")
        or "open"
    )
    page = int(
        data.get("moderator_complaint_scoped_page")
        or 0
    )

    if not complaint_id or len(reason) < 3:
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    (
        moderator_user_id,
        tenant_id,
        roles,
    ) = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
    ):
        await state.clear()
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            result = await ModerationService(
                ModerationRepository(session)
            ).add_complaint_target_scoped_blacklist(
                moderator_user_id=moderator_user_id,
                tenant_id=tenant_id,
                complaint_id=UUID(complaint_id),
                reason=reason,
            )

    except (ModerationError, ValueError) as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    logger.info(
        "complaint_scoped_blacklist_created "
        "telegram_id=%s complaint_id=%s "
        "blacklist_id=%s",
        callback.from_user.id,
        complaint_id,
        result.entity_id,
    )

    await state.clear()

    await callback.message.answer(
        t(
            "moderator_scoped_block_created",
            language,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_complaint_back_queue_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_CP_QUEUE:{view}:{page}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(F.data == "ADM_PAYMENTS")
async def list_pending_payments(callback: CallbackQuery, state: FSMContext):
    language = normalize_language(callback.from_user.language_code)
    await callback.answer(t("feature_disabled_beta_message", language), show_alert=True)

async def show_pending_payment(
    callback: CallbackQuery,
    state: FSMContext,
    index: int,
):
    data = await state.get_data()
    language = normalize_language(
        callback.from_user.language_code
    )
    ids = data.get("admin_payment_ids") or []

    if not ids:
        await callback.message.answer(
            t("admin_no_pending_payments", language),
            reply_markup=admin_panel_keyboard(language),
        )
        await callback.answer()
        return

    index = max(0, min(int(index), len(ids) - 1))

    admin_user_id, _, _ = await get_admin_user_context(
        callback.from_user.id
    )

    if not admin_user_id:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    try:
        async with get_session() as session:
            card = await BillingService(
                BillingRepository(session)
            ).get_pending_manual_payment_card(
                admin_user_id=admin_user_id,
                payment_id=UUID(ids[index]),
            )
    except (BillingError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_pending_payment_card(
            card,
            index=index,
            total=len(ids),
            language=language,
        ),
        reply_markup=pending_payment_keyboard(
            index,
            len(ids),
            language,
        ),
    )
    await callback.answer()


@admin_router.callback_query(F.data.startswith("ADM_PAY_VIEW:"))
async def view_pending_payment(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split(":", 1)[1])
    await show_pending_payment(callback, state, index=index)

@admin_router.callback_query(F.data.startswith("ADMIN_BETA_DISABLED:"))
async def show_admin_beta_disabled_feature(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)
    await callback.answer(t("feature_disabled_beta_message", language), show_alert=True)

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

@admin_router.callback_query(
    (F.data == "ADM_PORTFOLIO")
    | F.data.startswith("ADM_PORT_QUEUE:")
)
async def list_pending_portfolio(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    if callback.data == "ADM_PORTFOLIO":
        page = 0
    else:
        try:
            page = max(
                0,
                int((callback.data or "").split(":", 1)[1]),
            )
        except (TypeError, ValueError):
            await callback.answer(
                t("admin_item_not_found", language),
                show_alert=True,
            )
            return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
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
                moderator_user_id=moderator_user_id,
                page=page,
                page_size=MODERATOR_PORTFOLIO_PAGE_SIZE,
            )
    except PortfolioServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    visible_items = items[:MODERATOR_PORTFOLIO_PAGE_SIZE]
    has_next = len(items) > MODERATOR_PORTFOLIO_PAGE_SIZE

    await state.update_data(
        admin_portfolio_ids=[
            str(view.item.id)
            for view in visible_items
        ],
        admin_portfolio_page=page,
        admin_portfolio_has_next=has_next,
    )

    if not visible_items:
        await callback.message.answer(
            t("admin_no_pending_portfolio", language),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("moderator_back_btn", language),
                            callback_data="ADM_PANEL",
                        )
                    ]
                ]
            ),
        )
        await callback.answer()
        return

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

    page = int(data.get("admin_portfolio_page") or 0)
    has_next_page = bool(
        data.get("admin_portfolio_has_next")
    )

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

    await callback.answer()

    try:
        async with get_session() as session:

            items = await PortfolioService(
                PortfolioRepository(session)
            ).list_pending_items(
                tenant_id=tenant_id,
                moderator_user_id=admin_user_id,
                page=page,
                page_size=MODERATOR_PORTFOLIO_PAGE_SIZE,
            )

            items = items[:MODERATOR_PORTFOLIO_PAGE_SIZE]
    except PortfolioServiceError as exc:
        logger.warning(
            "moderator_portfolio_load_failed "
            "telegram_id=%s item_id=%s error=%s",
            callback.from_user.id,
            item_id,
            exc,
        )
        await callback.message.answer(
            t("moderator_portfolio_load_failed", language)
        )
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
        await callback.message.answer(
            t("admin_item_not_found", language)
        )
        return 
    
    text = format_portfolio_moderation_card(
        view,
        index=index,
        page=page,
        language=language,
    )

    keyboard = portfolio_moderation_keyboard(
        index=index,
        total=len(ids),
        page=page,
        has_next_page=has_next_page,
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

    page = 0

    text = format_portfolio_moderation_card(
        view,
        index=index,
        page=page,
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

    portfolio_status = t(
        f"portfolio_status_{item.status}",
        language,
    )

    await callback.message.answer(
        t("admin_portfolio_updated", language).format(
            status=portfolio_status,
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


@admin_router.callback_query(
    F.data.startswith("ADM_PORT_APPROVE:")
    | F.data.startswith("ADM_PORT_REJECT:")
)
async def ask_portfolio_moderation_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    try:
        prefix, raw_index = (callback.data or "").split(":", 1)
        index = int(raw_index)
    except (TypeError, ValueError):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    item_ids = data.get("admin_portfolio_ids") or []

    if index < 0 or index >= len(item_ids):
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    if prefix == "ADM_PORT_REJECT":
        await state.update_data(
            moderator_portfolio_item_id=item_ids[index],
            moderator_portfolio_index=index,
            moderator_portfolio_decision=None,
        )

        await callback.message.answer(
            t(
                "moderator_portfolio_reject_type_prompt",
                language,
            ),
            reply_markup=portfolio_reject_type_keyboard(
                language=language,
            ),
        )
        await callback.answer()
        return

    await state.update_data(
        moderator_portfolio_item_id=item_ids[index],
        moderator_portfolio_decision="approved",
        moderator_portfolio_index=index,
    )
    await state.set_state(
        AdminModerationFSM.entering_portfolio_moderation_reason
    )

    await callback.message.answer(
        t("moderator_portfolio_reason_prompt", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_PORT_DECISION_CANCEL:{index}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.in_(
        {
            "ADM_PORT_REJECT_REGULAR",
            "ADM_PORT_REJECT_FORBIDDEN",
        }
    )
)
async def choose_portfolio_reject_type(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()

    if not data.get("moderator_portfolio_item_id"):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    decision = (
        "forbidden"
        if callback.data
        == "ADM_PORT_REJECT_FORBIDDEN"
        else "rejected"
    )

    await state.update_data(
        moderator_portfolio_decision=decision,
    )
    await state.set_state(
        AdminModerationFSM
        .entering_portfolio_moderation_reason
    )

    await callback.message.answer(
        t(
            "moderator_portfolio_reason_prompt",
            language,
        )
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_PORT_REJECT_CANCEL"
)
async def cancel_portfolio_reject_type(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    index = int(
        data.get("moderator_portfolio_index")
        or 0
    )

    await state.set_state(None)
    await state.update_data(
        moderator_portfolio_item_id=None,
        moderator_portfolio_decision=None,
        moderator_portfolio_reason=None,
    )

    await callback.message.answer(
        t("moderator_portfolio_cancelled", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_PORT_VIEW:{index}"
                        ),
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.message(
    AdminModerationFSM.entering_portfolio_moderation_reason
)
async def receive_portfolio_moderation_reason(
    message: Message,
    state: FSMContext,
):
    language = normalize_language(message.from_user.language_code)
    reason = (message.text or "").strip()

    if len(reason) < 3:
        await message.answer(t("admin_reason_too_short", language))
        return

    data = await state.get_data()
    item_id = data.get("moderator_portfolio_item_id")
    decision = data.get("moderator_portfolio_decision")
    index = int(data.get("moderator_portfolio_index") or 0)

    if (
        not item_id
        or decision
        not in {"approved", "rejected", "forbidden"}
    ):
        await state.clear()
        await message.answer(t("admin_item_not_found", language))
        return

    await state.update_data(
        moderator_portfolio_reason=reason,
    )
    await state.set_state(
        AdminModerationFSM.confirming_portfolio_moderation
    )

    if decision == "approved":
        confirmation_key = (
            "moderator_portfolio_approve_confirmation"
        )
    elif decision == "forbidden":
        confirmation_key = (
            "moderator_portfolio_forbidden_confirmation"
        )
    else:
        confirmation_key = (
            "moderator_portfolio_reject_confirmation"
        )

    await message.answer(
        t(confirmation_key, language).format(reason=reason),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_portfolio_confirm_btn",
                            language,
                        ),
                        callback_data="ADM_PORT_DECISION_CONFIRM",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_portfolio_edit_reason_btn",
                            language,
                        ),
                        callback_data="ADM_PORT_DECISION_EDIT",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_changes_cancel_btn",
                            language,
                        ),
                        callback_data=(
                            f"ADM_PORT_DECISION_CANCEL:{index}"
                        ),
                    )
                ],
            ]
        ),
    )

@admin_router.callback_query(
    F.data == "ADM_PORT_DECISION_EDIT"
)
async def edit_portfolio_moderation_reason(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    if not data.get("moderator_portfolio_item_id"):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    await state.set_state(
        AdminModerationFSM.entering_portfolio_moderation_reason
    )
    await callback.message.answer(
        t("moderator_portfolio_reason_prompt", language)
    )
    await callback.answer()

@admin_router.callback_query(
    F.data.startswith("ADM_PORT_DECISION_CANCEL:")
)
async def cancel_portfolio_moderation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)

    try:
        index = max(
            0,
            int((callback.data or "").split(":", 1)[1]),
        )
    except (TypeError, ValueError):
        index = 0

    await state.set_state(None)
    await state.update_data(
        moderator_portfolio_item_id=None,
        moderator_portfolio_decision=None,
        moderator_portfolio_reason=None,
    )

    await callback.message.answer(
        t("moderator_portfolio_cancelled", language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("moderator_back_btn", language),
                        callback_data=f"ADM_PORT_VIEW:{index}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()

@admin_router.callback_query(
    F.data == "ADM_PORT_DECISION_CONFIRM"
)
async def confirm_portfolio_moderation(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(callback.from_user.language_code)
    data = await state.get_data()

    item_id = data.get("moderator_portfolio_item_id")
    decision = data.get("moderator_portfolio_decision")
    reason = (data.get("moderator_portfolio_reason") or "").strip()
    index = int(data.get("moderator_portfolio_index") or 0)
    item_ids = data.get("admin_portfolio_ids") or []

    if (
        not item_id
        or decision
        not in {"approved", "rejected", "forbidden"}
        or len(reason) < 3
    ):
        await state.clear()
        await callback.answer(
            t("admin_item_not_found", language),
            show_alert=True,
        )
        return

    moderator_user_id, tenant_id, roles = await get_admin_user_context(
        callback.from_user.id
    )

    if (
        not moderator_user_id
        or not tenant_id
        or not roles.intersection(ADMIN_MODERATION_MENU_ROLES)
    ):
        await state.clear()
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

            if decision == "approved":
                item = await service.approve_item(
                    tenant_id=tenant_id,
                    moderator_user_id=moderator_user_id,
                    item_id=UUID(item_id),
                    reason=reason,
                )

            elif decision == "forbidden":
                item = await service.reject_forbidden_item(
                    tenant_id=tenant_id,
                    moderator_user_id=moderator_user_id,
                    item_id=UUID(item_id),
                    reason=reason,
                )

            else:
                item = await service.reject_item(
                    tenant_id=tenant_id,
                    moderator_user_id=moderator_user_id,
                    item_id=UUID(item_id),
                    reason=reason,
                )

    except (PortfolioServiceError, ValueError) as exc:
        logger.warning(
            "moderator_portfolio_decision_failed "
            "telegram_id=%s item_id=%s decision=%s error=%s",
            callback.from_user.id,
            item_id,
            decision,
            exc,
        )
        await callback.answer(str(exc), show_alert=True)
        return

    logger.info(
        "moderator_portfolio_decision_completed "
        "telegram_id=%s moderator_user_id=%s "
        "item_id=%s decision=%s status=%s",
        callback.from_user.id,
        moderator_user_id,
        item_id,
        decision,
        item.status,
    )

    remaining_ids = [
        stored_id
        for stored_id in item_ids
        if stored_id != str(item_id)
    ]

    await state.set_state(None)
    await state.update_data(
        admin_portfolio_ids=remaining_ids,
        moderator_portfolio_item_id=None,
        moderator_portfolio_decision=None,
        moderator_portfolio_reason=None,
    )

    result_text_key = (
        "moderator_portfolio_approved"
        if decision == "approved"
        else "moderator_portfolio_rejected"
    )

    await callback.message.answer(
        t(result_text_key, language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(
                            "moderator_back_to_queue_btn",
                            language,
                        ),
                        callback_data="ADM_PORTFOLIO",
                    )
                ]
            ]
        ),
    )
    await callback.answer()