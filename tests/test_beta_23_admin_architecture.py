def test_admin_common_dependencies_are_extracted():
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    common_source = open(
        "handlers/admin_common.py",
        encoding="utf-8",
    ).read()

    assert (
        "from handlers.admin_common import"
        in admin_source
    )
    assert (
        "class AdminInterfaceLanguageMiddleware"
        not in admin_source
    )
    assert (
        "async def replace_admin_input_screen"
        not in admin_source
    )
    assert (
        "async def replace_admin_callback_screen"
        not in admin_source
    )

    assert (
        "class AdminInterfaceLanguageMiddleware"
        in common_source
    )
    assert (
        "async def replace_admin_input_screen"
        in common_source
    )
    assert (
        "async def replace_admin_callback_screen"
        in common_source
    )


def test_admin_finance_router_is_independent():
    finance_source = open(
        "handlers/admin_finance.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_finance_router = Router()"
        in finance_source
    )
    assert (
        "from handlers.admin import"
        not in finance_source
    )
    assert (
        "AdminFinanceFSM"
        in finance_source
    )
    assert (
        "from handlers.admin_finance import"
        in bot_source
    )
    assert (
        "dp.include_router(\n"
        "        admin_finance_router"
        in bot_source
    )


def test_admin_message_group_helper_is_shared():
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    common_source = open(
        "handlers/admin_common.py",
        encoding="utf-8",
    ).read()

    assert (
        "clear_admin_message_group,"
        in admin_source
    )
    assert (
        "async def clear_admin_message_group"
        not in admin_source
    )
    assert (
        "async def clear_admin_message_group"
        in common_source
    )


def test_admin_audit_router_is_independent():
    audit_source = open(
        "handlers/admin_audit.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_audit_router = Router()"
        in audit_source
    )
    assert (
        "from handlers.admin import"
        not in audit_source
    )
    assert (
        "ModerationRepository("
        not in audit_source
    )
    assert (
        "ModerationService("
        not in audit_source
    )
    assert (
        "AdminAuditService"
        in audit_source
    )
    assert (
        "def open_admin_audit_queue"
        not in admin_source
    )
    assert (
        "from handlers.admin_audit import"
        in bot_source
    )
    assert (
        "admin_audit_router"
        in bot_source
    )


def test_admin_users_router_is_independent():
    users_source = open(
        "handlers/admin_users.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert "admin_users_router = Router()" in users_source
    assert "from handlers.admin import" not in users_source
    assert "AdminUsersService" in users_source
    assert "ModerationRepository(" not in users_source
    assert "ModerationService(" not in users_source
    assert "get_admin_user_context(" not in users_source

    assert "def ask_admin_user_search" not in admin_source
    assert "def receive_admin_user_search" not in admin_source
    assert "def open_admin_user_details" not in admin_source
    assert "def open_admin_user_roles" not in admin_source
    assert "def open_admin_user_history" not in admin_source
    assert "def super_admin_users_start" not in admin_source
    assert "def super_admin_user_search_message" not in admin_source
    assert "def super_admin_open_user_card" not in admin_source
    assert "def super_admin_user_roles" not in admin_source
    assert "def super_admin_user_profile_alias" not in admin_source
    assert "def super_admin_read_only_admin_users_start" not in admin_source
    assert "def super_admin_read_only_admin_users_receive" not in admin_source
    assert "def super_admin_read_only_admin_user_open" not in admin_source
    assert "def super_admin_read_only_admin_user_roles" not in admin_source
    assert "def super_admin_read_only_admin_user_history" not in admin_source

    assert "from handlers.admin_users import" in bot_source
    assert "admin_users_router" in bot_source


def test_admin_specialists_router_is_independent():
    specialists_source = open(
        "handlers/admin_specialists.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_specialists_router = Router()"
        in specialists_source
    )
    assert (
        "from handlers.admin import"
        not in specialists_source
    )
    assert (
        "AdminSpecialistsService"
        in specialists_source
    )
    assert (
        "ModerationRepository("
        not in specialists_source
    )
    assert (
        "ModerationService("
        not in specialists_source
    )
    assert (
        "get_admin_user_context("
        not in specialists_source
    )

    assert (
        "def open_admin_specialists_list"
        not in admin_source
    )
    assert (
        "def open_admin_specialist_card"
        not in admin_source
    )
    assert (
        "def list_pending_profiles"
        not in admin_source
    )
    assert (
        "def show_pending_specialist"
        not in admin_source
    )

    moved_action_routes = (
        "ask_specialist_decision_reason",
        "ask_specialist_visibility_reason",
        "receive_specialist_visibility_reason",
        "edit_specialist_visibility_reason",
        "cancel_specialist_visibility",
        "confirm_specialist_visibility",
        "receive_specialist_decision_reason",
        "edit_specialist_decision_reason",
        "cancel_specialist_decision",
        "confirm_specialist_decision",
        "ask_specialist_changes_reason",
        "receive_specialist_changes_reason",
        "edit_specialist_changes_reason",
        "cancel_specialist_changes",
        "confirm_specialist_changes",
    )

    for function_name in moved_action_routes:
        assert (
            f"def {function_name}"
            not in admin_source
        )
        assert (
            f"def {function_name}"
            in specialists_source
        )

    assert (
        "from handlers.admin_specialists import"
        in bot_source
    )
    assert (
        "admin_specialists_router"
        in bot_source
    )

def test_admin_complaints_router_is_independent():
    complaints_source = open(
        "handlers/admin_complaints.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    scoped_blacklist_source = open(
        "handlers/admin_scoped_blacklist.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_complaints_router = Router()"
        in complaints_source
    )
    assert (
        "from handlers.admin import"
        not in complaints_source
    )
    assert (
        "AdminComplaintsService"
        in complaints_source
    )
    assert (
        "ModerationRepository("
        not in complaints_source
    )
    assert (
        "ModerationService("
        not in complaints_source
    )
    assert (
        "get_admin_user_context("
        not in complaints_source
    )

    moved_routes = (
        "list_open_complaints",
        "change_complaints_queue",
        "show_complaints_filter",
        "take_complaint_from_queue",
        "view_complaint",
        "ask_review_complaint_reason",
        "ask_resolve_complaint_reason",
        "ask_reject_complaint_reason",
        "receive_complaint_resolution_reason",
        "ask_complaint_admin_reason",
        "receive_complaint_admin_reason",
        "edit_complaint_admin_reason",
        "cancel_complaint_admin_escalation",
        "confirm_complaint_admin_escalation",
        "super_admin_read_only_moderator_complaints",
        "super_admin_read_only_moderator_complaint",
    )

    for function_name in moved_routes:
        assert (
            f"def {function_name}"
            in complaints_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )

    scoped_blacklist_routes = (
        "ask_complaint_scoped_block_reason",
        "receive_complaint_scoped_block_reason",
        "edit_complaint_scoped_block_reason",
        "cancel_complaint_scoped_block",
        "confirm_complaint_scoped_block",
    )

    for function_name in scoped_blacklist_routes:
        assert (
            f"def {function_name}"
            in scoped_blacklist_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )
        assert (
            f"def {function_name}"
            not in complaints_source
        )

    assert (
        "class AdminComplaintsFSM"
        in complaints_source
    )
    assert (
        "entering_complaint_scoped_block_reason"
        not in complaints_source
    )
    assert (
        "from handlers.admin_complaints import"
        in scoped_blacklist_source
    )
    assert (
        "complaint_resolution_result_keyboard,"
        in scoped_blacklist_source
    )
    assert (
        "complaint_resolution_result_keyboard,"
        not in admin_source
    )
    assert (
        "from handlers.admin_complaints import"
        in bot_source
    )
    assert (
        "admin_complaints_router"
        in bot_source
    )

def test_admin_scoped_blacklist_router_is_independent():
    scoped_source = open(
        "handlers/admin_scoped_blacklist.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_scoped_blacklist_router = Router()"
        in scoped_source
    )
    assert (
        "from handlers.admin import"
        not in scoped_source
    )
    assert (
        "AdminScopedBlacklistService"
        in scoped_source
    )
    assert (
        "ModerationRepository("
        not in scoped_source
    )
    assert (
        "ModerationService("
        not in scoped_source
    )
    assert (
        "get_admin_user_context("
        not in scoped_source
    )

    moved_routes = (
        "ask_blacklist_add_user",
        "receive_blacklist_add_user",
        "edit_blacklist_add_reason",
        "cancel_blacklist_add",
        "confirm_blacklist_add",
        "receive_blacklist_add_reason",
        "super_admin_read_only_moderator_blacklist",
        "ask_specialist_scoped_block_reason",
        "receive_specialist_scoped_block_reason",
        "edit_specialist_scoped_block_reason",
        "cancel_specialist_scoped_block",
        "confirm_specialist_scoped_block",
        "open_active_scoped_blacklist",
        "change_scoped_blacklist_queue",
        "ask_scoped_blacklist_revoke_reason",
        "receive_scoped_blacklist_revoke_reason",
        "edit_scoped_blacklist_revoke_reason",
        "cancel_scoped_blacklist_revoke",
        "confirm_scoped_blacklist_revoke",
        "ask_complaint_scoped_block_reason",
        "receive_complaint_scoped_block_reason",
        "edit_complaint_scoped_block_reason",
        "cancel_complaint_scoped_block",
        "confirm_complaint_scoped_block",
    )

    for function_name in moved_routes:
        assert (
            f"def {function_name}"
            in scoped_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )

    global_source = open(
        "handlers/super_admin_global_blacklist.py",
        encoding="utf-8",
    ).read()

    global_routes = (
        "open_super_admin_global_blacklist",
        "execute_super_admin_global_blacklist_add",
        "execute_super_admin_global_blacklist_revoke",
        "open_super_admin_global_blacklist_queue",
    )

    for function_name in global_routes:
        assert (
            f"def {function_name}"
            in global_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )
        assert (
            f"def {function_name}"
            not in scoped_source
        )

    assert (
        "class AdminScopedBlacklistFSM"
        in scoped_source
    )
    assert (
        "entering_super_admin_global_blacklist_add"
        not in scoped_source
    )
    assert (
        "from handlers.admin_complaints import"
        in scoped_source
    )
    assert (
        "from handlers.admin_scoped_blacklist import"
        in bot_source
    )
    assert (
        "admin_scoped_blacklist_router"
        in bot_source
    )


def test_admin_dictionaries_router_is_independent():
    import ast

    dictionary_source = open(
        "handlers/admin_dictionaries.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_dictionaries_router = Router()"
        in dictionary_source
    )
    assert (
        "from handlers.admin import"
        not in dictionary_source
    )
    assert (
        "AdminDictionariesFSM"
        in dictionary_source
    )
    assert (
        "AdminModerationFSM"
        not in dictionary_source
    )
    assert (
        "AdminDictionariesService"
        in dictionary_source
    )
    assert (
        "get_admin_user_context("
        not in dictionary_source
    )
    assert (
        "DictionaryRepository("
        not in dictionary_source
    )
    assert (
        "DictionaryService("
        not in dictionary_source
    )

    dictionary_tree = ast.parse(
        dictionary_source
    )
    async_functions = [
        node
        for node in dictionary_tree.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
    ]

    assert len(async_functions) == 89
    assert (
        "def admin_dictionaries_menu"
        not in admin_source
    )
    assert (
        "def admin_categories_dictionary"
        not in admin_source
    )
    assert (
        "def admin_professions_dictionary"
        not in admin_source
    )
    assert (
        "def admin_geo_dictionary"
        not in admin_source
    )
    assert (
        "def admin_languages_dictionary"
        not in admin_source
    )
    assert (
        "def admin_skills_dictionary"
        not in admin_source
    )

    assert (
        "from handlers.admin_dictionaries import"
        in bot_source
    )
    assert (
        bot_source.index(
            "admin_dictionaries_router\n"
            "    )"
        )
        < bot_source.index(
            "dp.include_router(admin_router)"
        )
    )



def test_admin_panel_keyboard_is_shared():
    import ast
    from pathlib import Path

    admin_source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    common_source = Path(
        "handlers/admin_common.py"
    ).read_text(encoding="utf-8")

    admin_tree = ast.parse(admin_source)
    common_tree = ast.parse(common_source)

    assert not any(
        isinstance(node, ast.FunctionDef)
        and node.name == "admin_panel_keyboard"
        for node in admin_tree.body
    )
    assert any(
        isinstance(node, ast.FunctionDef)
        and node.name == "admin_panel_keyboard"
        for node in common_tree.body
    )
    assert (
        "from handlers.admin_common import ("
        in admin_source
    )
    assert (
        "    admin_panel_keyboard,"
        in admin_source
    )


def test_admin_portfolio_router_is_independent():
    import ast

    from handlers.admin_portfolio import (
        admin_portfolio_router,
    )

    portfolio_source = open(
        "handlers/admin_portfolio.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_portfolio_router = Router()"
        in portfolio_source
    )
    assert (
        "from handlers.admin import"
        not in portfolio_source
    )
    assert (
        "AdminPortfolioService"
        in portfolio_source
    )
    portfolio_tree = ast.parse(
        portfolio_source
    )
    direct_calls = {
        node.func.id
        for node in ast.walk(portfolio_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
    }

    assert (
        "PortfolioRepository"
        not in direct_calls
    )
    assert (
        "PortfolioService"
        not in direct_calls
    )
    assert (
        "get_admin_user_context"
        not in direct_calls
    )

    moved_functions = (
        "portfolio_moderation_keyboard",
        "rejected_portfolio_keyboard",
        "portfolio_reject_type_keyboard",
        "format_portfolio_moderation_card",
        (
            "super_admin_read_only_"
            "moderator_portfolio"
        ),
        (
            "show_super_admin_read_only_"
            "portfolio_item"
        ),
        "list_pending_portfolio",
        "show_pending_portfolio_item",
        "list_rejected_portfolio",
        "show_rejected_portfolio_item",
        "restore_rejected_portfolio_item",
        "ask_portfolio_moderation_reason",
        "receive_portfolio_moderation_reason",
        "confirm_portfolio_moderation",
        "replace_admin_photo_screen",
    )

    for function_name in moved_functions:
        assert (
            f"def {function_name}"
            in portfolio_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )

    assert (
        "class AdminPortfolioFSM"
        in portfolio_source
    )
    assert (
        "AdminModerationFSM"
        not in portfolio_source
    )
    assert (
        "entering_portfolio_moderation_reason"
        not in admin_source
    )
    assert (
        "confirming_portfolio_moderation"
        not in admin_source
    )
    assert (
        "from handlers.admin_portfolio import ("
        in bot_source
    )
    assert (
        "dp.include_router("
        "admin_portfolio_router"
        ")"
        in bot_source
    )
    assert (
        len(
            admin_portfolio_router
            .callback_query.handlers
        )
        == 13
    )
    assert (
        len(
            admin_portfolio_router
            .message.handlers
        )
        == 1
    )


def test_admin_reviews_router_is_independent():
    import ast

    from handlers.admin_reviews import (
        admin_reviews_router,
    )

    reviews_source = open(
        "handlers/admin_reviews.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_reviews_router = Router()"
        in reviews_source
    )
    assert (
        "from handlers.admin import"
        not in reviews_source
    )
    assert (
        "AdminReviewsService"
        in reviews_source
    )

    reviews_tree = ast.parse(
        reviews_source
    )
    direct_calls = {
        node.func.id
        for node in ast.walk(reviews_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
    }

    assert "ReviewRepository" not in direct_calls
    assert "ReviewService" not in direct_calls
    assert (
        "get_admin_user_context"
        not in direct_calls
    )

    moved_functions = (
        "review_moderation_error_text",
        "review_keyboard",
        "review_reason_keyboard",
        "review_result_keyboard",
        "format_review_card",
        (
            "super_admin_read_only_"
            "moderator_reviews"
        ),
        (
            "show_super_admin_read_only_review"
        ),
        "list_pending_reviews",
        "open_pending_reviews_page",
        "show_review",
        "approve_pending_review",
        "prepare_review_moderation_reason",
        "receive_review_moderation_reason",
    )

    for function_name in moved_functions:
        assert (
            f"def {function_name}"
            in reviews_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )

    assert "class AdminReviewsFSM" in reviews_source
    assert (
        "AdminModerationFSM"
        not in reviews_source
    )
    assert (
        "entering_review_hide_reason"
        not in admin_source
    )
    assert (
        "from handlers.admin_reviews import ("
        in bot_source
    )
    assert (
        "dp.include_router("
        "admin_reviews_router"
        ")"
        in bot_source
    )
    assert (
        len(
            admin_reviews_router
            .callback_query.handlers
        )
        == 8
    )
    assert (
        len(
            admin_reviews_router
            .message.handlers
        )
        == 1
    )

def test_admin_governance_router_is_independent():
    governance_source = open(
        "handlers/admin_governance.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_governance_router = Router()"
        in governance_source
    )
    assert (
        "from handlers.admin import"
        not in governance_source
    )
    assert (
        "AdminGovernanceService"
        in governance_source
    )
    assert (
        "get_admin_user_context("
        not in governance_source
    )
    assert (
        "ModerationRepository("
        not in governance_source
    )
    assert (
        "ModerationService("
        not in governance_source
    )
    assert (
        "AdminModerationFSM"
        not in governance_source
    )

    moved_routes = (
        "open_super_admin_role_scopes",
        "open_super_admin_user_role_scopes",
        "change_super_admin_role_scopes_queue",
        "super_admin_permissions",
        "super_admin_permission_search_start",
        "super_admin_permission_search_message",
        "super_admin_permission_grant_start",
        "super_admin_permission_revoke_start",
        "super_admin_permission_history",
        "super_admin_permission_grant_receive",
        "super_admin_permission_revoke_receive",
        "super_admin_permission_cancel",
        "super_admin_permission_grant_confirm",
        "super_admin_permission_revoke_confirm",
        "super_admin_role_scope_alias",
    )

    for function_name in moved_routes:
        assert (
            f"def {function_name}"
            in governance_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )

    assert (
        "admin_governance_router"
        in bot_source
    )

    governance_position = bot_source.index(
        "dp.include_router(admin_governance_router)"
    )
    admin_position = bot_source.index(
        "dp.include_router(admin_router)"
    )

    assert governance_position < admin_position

def test_admin_dialogs_router_is_independent():
    dialogs_source = open(
        "handlers/admin_dialogs.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_dialogs_router = Router()"
        in dialogs_source
    )
    assert (
        "from handlers.admin import"
        not in dialogs_source
    )
    assert (
        "AdminDialogsService"
        in dialogs_source
    )

    for forbidden in (
        "get_admin_user_context(",
        "ModerationRepository(",
        "ModerationService(",
        "ContactChatRepository(",
        "ContactChatService(",
    ):
        assert forbidden not in dialogs_source

    moved_routes = (
        "super_admin_read_only_client_dialogs",
        "super_admin_read_only_client_dialog_open",
        "super_admin_read_only_specialist_dialogs",
        "super_admin_read_only_specialist_dialog_open",
        "admin_dialogs_entry",
        "open_admin_dialog_thread",
    )

    for function_name in moved_routes:
        assert (
            f"def {function_name}"
            in dialogs_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )

    assert (
        "admin_dialogs_router"
        in bot_source
    )

    dialogs_position = bot_source.index(
        "dp.include_router(admin_dialogs_router)"
    )
    admin_position = bot_source.index(
        "dp.include_router(admin_router)"
    )

    assert dialogs_position < admin_position


def test_admin_impersonation_router_is_independent():
    from handlers.admin_impersonation import (
        admin_impersonation_router,
    )

    impersonation_source = open(
        "handlers/admin_impersonation.py",
        encoding="utf-8",
    ).read()
    admin_source = open(
        "handlers/admin.py",
        encoding="utf-8",
    ).read()
    bot_source = open(
        "bot.py",
        encoding="utf-8",
    ).read()

    assert (
        "admin_impersonation_router = Router()"
        in impersonation_source
    )
    assert (
        "from handlers.admin import"
        not in impersonation_source
    )
    assert (
        "AdminImpersonationService"
        in impersonation_source
    )
    assert (
        "AdminModerationFSM"
        not in impersonation_source
    )
    assert (
        "class AdminImpersonationFSM"
        in impersonation_source
    )

    moved_routes = (
        "super_admin_read_only_admin_specialists",
        "super_admin_read_only_specialist_cabinets",
        "super_admin_read_only_specialist_cabinet_open",
        "super_admin_read_only_moderator_home",
        "super_admin_read_only_moderator_queue",
        "super_admin_read_only_moderator_profile",
        "super_admin_read_only_admin_home",
        "super_admin_read_only_admin_specialist_open",
        "super_admin_read_only_admin_moderation",
        "super_admin_read_only_client_home",
        "super_admin_read_only_specialist_profile",
        "super_admin_read_only_specialist_home",
        "super_admin_impersonation_menu",
        "super_admin_impersonation_start",
        "super_admin_impersonation_reason_receive",
        "super_admin_impersonation_role",
        "super_admin_impersonation_stop",
    )

    for function_name in moved_routes:
        assert (
            f"def {function_name}"
            in impersonation_source
        )
        assert (
            f"def {function_name}"
            not in admin_source
        )

    assert len(
        admin_impersonation_router
        .callback_query.handlers
    ) == 16
    assert len(
        admin_impersonation_router
        .message.handlers
    ) == 1

    impersonation_position = bot_source.index(
        "dp.include_router("
        "admin_impersonation_router"
        ")"
    )
    admin_position = bot_source.index(
        "dp.include_router(admin_router)"
    )

    assert impersonation_position < admin_position




def test_admin_language_middleware_uses_application_service():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_common.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    middleware = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.ClassDef,
        )
        and node.name
        == "AdminInterfaceLanguageMiddleware"
    )
    method = next(
        node
        for node in middleware.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name == "__call__"
    )
    block = (
        ast.get_source_segment(
            source,
            method,
        )
        or ""
    )

    called_names = {
        call.func.id
        for call in ast.walk(method)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Name,
        )
    }
    called_methods = {
        call.func.attr
        for call in ast.walk(method)
        if isinstance(call, ast.Call)
        and isinstance(
            call.func,
            ast.Attribute,
        )
    }

    assert (
        "UserSettingsService"
        in called_names
    )
    assert "get_context" in called_methods
    assert "UserService(" not in block
    assert "Repository(" not in block
    assert "commit" not in called_methods
    assert "rollback" not in called_methods
