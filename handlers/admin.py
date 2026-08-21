import logging

from aiogram import (
    F,
    Router,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from database.models import (
    AdminAction,
    EventLog,
)
from database.session import get_session
from handlers.admin_specialists import (
    admin_specialist_filter_keyboard,
    show_pending_specialist,
)
from handlers.admin_users import (
    super_admin_user_roles,
)
from handlers.admin_support import (
    format_support_staff_menu,
    support_staff_menu_keyboard,
)
from handlers.admin_common import (
    ADMIN_DIALOGS_MENU_ROLES,
    ADMIN_DICT_MENU_ROLES,
    ADMIN_GLOBAL_BLACKLIST_ROLES,
    ADMIN_LOG_MENU_ROLES,
    ADMIN_PAYMENT_MENU_ROLES,
    ADMIN_PROMOTION_MENU_ROLES,
    ADMIN_ROLE_MENU_ROLES,
    ADMIN_SUPPORT_MENU_ROLES,
    ADMIN_SUPPORT_STATS_ROLES,
    ADMIN_SYSTEM_MENU_ROLES,
    admin_panel_keyboard,
    ADMIN_MODERATION_MENU_ROLES,
    AdminInterfaceLanguageMiddleware,
    clear_admin_message_group,
    format_admin_menu,
    normalize_admin_language,
    replace_admin_callback_screen,
)
from handlers.admin_audit import (
    open_super_admin_audit_queue,
)
from handlers.start import (
    normalize_language as normalize_base_language,
    send_global_main_menu,
)
from services.admin_governance import (
    AdminGovernanceAccessError,
    AdminGovernanceService,
)
from services.admin_panel import (
    AdminPanelAccessError,
    AdminPanelService,
)
from services.admin_system import (
    AdminSystemAccessError,
    AdminSystemService,
)
from services.admin_specialists import (
    AdminSpecialistsAccessError,
    AdminSpecialistsService,
)
from services.admin_support import (
    AdminSupportAccessError,
)
from services.moderation import (
    ModerationError,
    AdminMenuSummary,
    SuperAdminMenuSummary,
)
from services.support import SupportServiceError
from ui.texts import t
from utils.telegram_cleanup import (
    delete_telegram_messages,
    edit_or_replace_menu_message,
)

admin_router = Router()
logger = logging.getLogger(__name__)


admin_router.callback_query.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)
admin_router.message.outer_middleware(
    AdminInterfaceLanguageMiddleware()
)


normalize_language = normalize_admin_language

ROOT_ONLY_ADMIN_CALLBACKS = frozenset(
    {
        "SA_ROLE_GRANT",
        "SA_ROLE_REVOKE",
        "SA_ROLE_CANCEL",
        "SA_ROLE_GRANT_CONFIRM",
        "SA_ROLE_GRANT_FINAL",
        "SA_ROLE_REVOKE_CONFIRM",
        "SA_ROLE_REVOKE_FINAL",
        "SA_SCOPE_ADD",
        "SA_SCOPE_ADD_USER",
        "SA_SCOPE_ADD_CONFIRM",
        "SA_SCOPE_ADD_CANCEL",
        "SA_SCOPE_REVOKE_CONFIRM",
        "SA_SCOPE_REVOKE_CANCEL",
        "ADM_ROLE_GRANT",
        "ADM_ROLE_REVOKE",
    }
)


@admin_router.callback_query(
    F.data.in_(ROOT_ONLY_ADMIN_CALLBACKS)
    | F.data.startswith("SA_SCOPE_REVOKE:")
)
async def reject_root_only_admin_mutation(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()
    await callback.answer(
        t(
            "admin_access_denied",
            normalize_language(
                callback.from_user.language_code
            ),
        ),
        show_alert=True,
    )




READ_ONLY_CLIENT_PAGE_SIZE = 5
READ_ONLY_SPECIALIST_CABINETS_PAGE_SIZE = 5
class AdminModerationFSM(StatesGroup):
    entering_super_admin_impersonation_admin_user_search = (
        State()
    )

def admin_roles_keyboard(
    language: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(
                        "admin_panel_back",
                        language,
                    ),
                    callback_data="ADM_PANEL",
                )
            ]
        ]
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

    try:
        async with get_session() as session:
            items = await AdminSystemService(
                session
            ).list_smoke_definitions(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSystemAccessError:
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

    try:
        async with get_session() as session:
            await AdminSystemService(
                session
            ).list_smoke_definitions(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSystemAccessError:
        await callback.answer(
            t("admin_access_denied", language),
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
            result = await AdminSystemService(
                session
            ).run_smoke_tests(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSystemAccessError:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=super_admin_smoke_keyboard(
                language
            ),
            callback_answered=True,
        )
        return

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

    try:
        async with get_session() as session:
            await AdminSystemService(
                session
            ).list_smoke_definitions(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSystemAccessError:
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

    try:
        async with get_session() as session:
            await AdminSystemService(
                session
            ).list_smoke_definitions(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSystemAccessError:
        await callback.answer(
            t("admin_access_denied", language),
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
            result = await AdminSystemService(
                session
            ).run_smoke_tests(
                platform_user_id=(
                    callback.from_user.id
                ),
                selected_code=selected_code,
            )

    except AdminSystemAccessError:
        await replace_admin_callback_screen(
            callback=callback,
            state=state,
            text=t(
                "admin_access_denied",
                language,
            ),
            reply_markup=super_admin_smoke_keyboard(
                language
            ),
            callback_answered=True,
        )
        return

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

    try:
        async with get_session() as session:
            items = await AdminSystemService(
                session
            ).list_smoke_history(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSystemAccessError:
        await callback.answer(
            t("admin_access_denied", language),
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

    try:
        async with get_session() as session:
            card = await AdminSystemService(
                session
            ).get_system_status(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSystemAccessError:
        await callback.answer(
            t("admin_access_denied", language),
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

    try:
        async with get_session() as session:
            card = await AdminSystemService(
                session
            ).get_system_status(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSystemAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
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



def format_super_admin_menu(
    summary: SuperAdminMenuSummary,
    language: str,
) -> str:
    return t(
        "super_admin_menu_text",
        language,
    ).format(
        users=summary.users,
        professional_cabinets=(
            summary.professional_cabinets
        ),
        tickets=summary.tickets,
        complaints=summary.complaints,
        global_blacklist=(
            summary.global_blacklist
        ),
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
                    count=summary.professional_cabinets,
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
                text=t(
                    "admin_global_blacklist_btn",
                    language,
                ).format(
                    count=summary.global_blacklist,
                ),
                callback_data="SA_GLOBAL_BLACKLIST",
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
    callback: CallbackQuery,
    state: FSMContext,
    *,
    callback_answered: bool = False,
):
    user = callback.from_user
    language = normalize_language(
        user.language_code
    )

    async def send_panel(
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        menu_message = await edit_or_replace_menu_message(
            callback=callback,
            text=text,
            reply_markup=reply_markup,
        )

        await state.update_data(
            last_menu_message_id=menu_message.message_id,
        )

        return menu_message

    if not callback_answered:
        await callback.answer()
        callback_answered = True

    try:
        async with get_session() as session:
            panel = await AdminPanelService(
                session
            ).open_panel(
                platform_user_id=user.id,
            )

    except (
        AdminPanelAccessError,
        AdminSupportAccessError,
    ):
        await send_panel(
            t("admin_access_denied", language)
        )
        return

    except (
        ModerationError,
        SupportServiceError,
    ) as exc:
        await send_panel(str(exc))
        return

    language = normalize_base_language(
        panel.language_code
    )

    state_data = await state.get_data()

    await delete_telegram_messages(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
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

    if panel.panel_type == "super_admin":
        await send_panel(
            format_super_admin_menu(
                panel.payload,
                language,
            ),
            reply_markup=super_admin_menu_keyboard(
                panel.payload,
                language,
                show_role_switch=(
                    panel.show_role_switch
                ),
            ),
        )
        return

    if panel.panel_type == "admin":
        await send_panel(
            format_admin_menu(
                panel.payload,
                language,
            ),
            reply_markup=minimal_admin_menu_keyboard(
                panel.payload,
                language,
                show_role_switch=(
                    panel.show_role_switch
                ),
            ),
        )
        return

    if panel.panel_type == "support":
        await send_panel(
            format_support_staff_menu(
                panel.payload.counts,
                language,
            ),
            reply_markup=support_staff_menu_keyboard(
                language,
                show_role_switch=(
                    panel.show_role_switch
                ),
            ),
        )
        return

    if panel.panel_type == "moderator":
        await send_panel(
            format_moderator_menu(
                panel.payload,
                language,
            ),
            reply_markup=moderator_menu_keyboard(
                panel.payload,
                language,
                show_role_switch=(
                    panel.show_role_switch
                ),
            ),
        )
        return

    panel_text = t(
        "admin_panel_title",
        language,
    )

    if not (
        panel.panel_roles.intersection(
            ADMIN_MODERATION_MENU_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_PAYMENT_MENU_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_ROLE_MENU_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_LOG_MENU_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_SUPPORT_MENU_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_SUPPORT_STATS_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_DICT_MENU_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_DIALOGS_MENU_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_PROMOTION_MENU_ROLES
        )
        or panel.panel_roles.intersection(
            ADMIN_SYSTEM_MENU_ROLES
        )
    ):
        panel_text = t(
            "admin_no_available_actions",
            language,
        )

    await send_panel(
        panel_text,
        reply_markup=admin_panel_keyboard(
            language,
            panel.panel_roles,
            show_role_switch=(
                panel.show_role_switch
            ),
        ),
    )

@admin_router.callback_query(F.data == "ADM_PANEL")
async def admin_panel_callback(
    callback: CallbackQuery,
    state: FSMContext,
):
    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key="admin_audit_message_ids",
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "super_admin_audit_message_ids"
        ),
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "admin_escalated_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "support_staff_ticket_message_ids"
        ),
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key="admin_user_message_ids",
        preserve_current=True,
    )

    await clear_admin_message_group(
        callback=callback,
        state=state,
        state_key=(
            "admin_user_history_message_ids"
        ),
        preserve_current=True,
    )

    await show_admin_panel(
        callback,
        state,
    )
















@admin_router.callback_query(
    F.data == "ADM_PROMOTION_STUB"
)
async def admin_promotion_section_stub(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
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
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await AdminGovernanceService(
                session
            ).require_super_admin_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminGovernanceAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    data = await state.get_data()

    if data.get(
        "super_admin_selected_user_id"
    ):
        await super_admin_user_roles(
            callback,
            state,
        )
        return

    await state.set_state(
        AdminModerationFSM
        .waiting_super_admin_user_search
    )

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "super_admin_user_search_prompt",
            language,
        ),
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

    try:
        async with get_session() as session:
            result = await AdminPanelService(
                session
            ).open_moderation_menu(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminPanelAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    except ModerationError as exc:
        await callback.answer(
            str(exc),
            show_alert=True,
        )
        return

    await state.clear()

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=format_moderator_menu(
            result.summary,
            language,
        ),
        reply_markup=moderator_menu_keyboard(
            result.summary,
            language,
            show_role_switch=False,
            show_specialist_management=bool(
                result.roles.intersection(
                    {
                        "admin",
                        "super_admin",
                    }
                )
            ),
            back_callback="ADM_PANEL",
        ),
    )


@admin_router.callback_query(F.data == "ADM_ROLES")
async def admin_roles_panel(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await AdminGovernanceService(
                session
            ).require_super_admin_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminGovernanceAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await state.clear()

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_roles_title",
            language,
        ),
        reply_markup=admin_roles_keyboard(
            language
        ),
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
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )

    try:
        async with get_session() as session:
            await AdminSpecialistsService(
                session
            ).require_admin_actor(
                platform_user_id=(
                    callback.from_user.id
                ),
            )

    except AdminSpecialistsAccessError:
        await callback.answer(
            t("admin_access_denied", language),
            show_alert=True,
        )
        return

    await replace_admin_callback_screen(
        callback=callback,
        state=state,
        text=t(
            "admin_specialist_filter_title",
            language,
        ),
        reply_markup=(
            admin_specialist_filter_keyboard(
                language
            )
        ),
    )


@admin_router.callback_query(
    F.data.startswith("ADM_SP_OPEN:")
)
async def open_pending_specialist(
    callback: CallbackQuery,
    state: FSMContext,
):
    language = normalize_language(
        callback.from_user.language_code
    )
    data = await state.get_data()
    cabinet_ids = (
        data.get(
            "admin_pending_professional_cabinet_ids"
        )
        or []
    )

    try:
        index = int(
            (callback.data or "").split(
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

    if (
        index < 0
        or index >= len(cabinet_ids)
    ):
        await callback.answer(
            t(
                "admin_item_not_found",
                language,
            ),
            show_alert=True,
        )
        return

    await show_pending_specialist(
        callback,
        state,
        index=index,
    )

@admin_router.callback_query(F.data.startswith("ADMIN_BETA_DISABLED:"))
async def show_admin_beta_disabled_feature(callback: CallbackQuery):
    language = normalize_language(callback.from_user.language_code)
    await callback.answer(t("feature_disabled_beta_message", language), show_alert=True)
