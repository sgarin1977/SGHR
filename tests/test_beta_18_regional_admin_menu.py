import pytest

from handlers.admin import (
    ADMIN_GLOBAL_BLACKLIST_ROLES,
    ADMIN_ROLE_MENU_ROLES,
    format_admin_menu,
    minimal_admin_menu_keyboard,
    super_admin_menu_keyboard,
    moderator_menu_keyboard,
)
from handlers.admin_governance import (
    super_admin_role_scopes_keyboard,
)
from handlers.admin_users import (
    super_admin_user_roles_keyboard,
)
from services.moderation import (
    AdminMenuSummary,
    ModeratorMenuSummary,
    SuperAdminMenuSummary,
)


def callback_values(markup):
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }


@pytest.mark.parametrize(
    "language",
    ("ru", "en", "pt", "uk", "pl", "de", "nl"),
)
def test_regional_admin_menu_hides_global_blacklist(
    language,
):
    summary = AdminMenuSummary(
        users=64,
        professional_cabinets=173,
        tickets=2,
        complaints=3,
        blacklist=7,
        audit_alerts=1,
    )

    text = format_admin_menu(
        summary,
        language,
    )
    callbacks = callback_values(
        minimal_admin_menu_keyboard(
            summary,
            language,
            show_role_switch=False,
        )
    )

    assert "{blacklist}" not in text
    assert "ADM_GLOBAL_BLACKLIST" not in callbacks


def test_scoped_blacklist_remains_in_moderation():
    summary = ModeratorMenuSummary(
        profiles=0,
        portfolio=0,
        reviews=0,
        complaints=0,
        blacklist=7,
    )

    callbacks = callback_values(
        moderator_menu_keyboard(
            summary,
            "en",
            show_role_switch=False,
        )
    )

    assert "ADM_SCOPED_BLACKLIST" in callbacks


def test_global_blacklist_is_super_admin_only():
    assert ADMIN_GLOBAL_BLACKLIST_ROLES == {
        "super_admin"
    }

def test_super_admin_role_menu_is_hidden():
    assert ADMIN_ROLE_MENU_ROLES == set()


def test_super_admin_user_roles_are_read_only():
    callbacks = callback_values(
        super_admin_user_roles_keyboard("en")
    )

    assert "SA_ROLE_GRANT" not in callbacks
    assert "SA_ROLE_REVOKE" not in callbacks
    assert "SA_ROLE_SCOPE" in callbacks
    assert "SA_ROLE_HISTORY" in callbacks


def test_super_admin_scope_list_is_read_only():
    callbacks = callback_values(
        super_admin_role_scopes_keyboard(
            view="active",
            page=0,
            has_next=False,
            user_filtered=False,
            language="en",
        )
    )

    assert "SA_SCOPE_ADD" not in callbacks
    assert "SA_SCOPE_ADD_USER" not in callbacks
    assert not any(
        value.startswith("SA_SCOPE_REVOKE:")
        for value in callbacks
    )



def test_impersonated_admin_has_no_global_blacklist():
    import ast
    from pathlib import Path

    source = Path(
        "handlers/admin_impersonation.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()

    menu = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and (
            item.name
            == "super_admin_read_only_admin_menu_keyboard"
        )
    )
    menu_block = "\n".join(
        lines[
            menu.lineno - 1:
            menu.end_lineno
        ]
    )

    assert (
        "SA_RO_ADMIN_GLOBAL_BLACKLIST"
        not in menu_block
    )
    assert (
        "def super_admin_read_only_admin_global_blacklist"
        not in source
    )
    assert (
        "def open_super_admin_global_blacklist"
        in Path(
            "handlers/"
            "super_admin_global_blacklist.py"
        ).read_text(encoding="utf-8")
    )

@pytest.mark.parametrize(
    "language",
    ("ru", "en", "pt", "uk", "pl", "de", "nl"),
)
def test_regional_user_card_hides_global_blacklist(
    language,
):
    from handlers.admin_users import (
        admin_user_details_keyboard,
    )
    from ui.texts import t

    keyboard = admin_user_details_keyboard(
        index=0,
        language=language,
    )
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }

    assert not any(
        "GLOBAL_BLOCK" in callback
        or "GLOBAL_UNBLOCK" in callback
        for callback in callbacks
    )
    assert "{blacklist}" not in t(
        "admin_user_details",
        language,
    )


def test_obsolete_regional_global_blacklist_routes_removed():
    from pathlib import Path

    admin_source = Path(
        "handlers/admin.py"
    ).read_text(encoding="utf-8")
    users_source = Path(
        "handlers/admin_users.py"
    ).read_text(encoding="utf-8")

    obsolete_functions = (
        "open_active_global_blacklist",
        "change_global_blacklist_queue",
        "open_global_blacklist_queue",
        "ask_admin_user_global_unblock_reason",
        "execute_admin_user_global_unblock",
        "ask_admin_user_global_block_reason",
        "execute_admin_user_global_block",
    )

    for function_name in obsolete_functions:
        assert (
            f"def {function_name}("
            not in admin_source
        )

    combined = admin_source + users_source

    assert "ADM_GLOBAL_BLACKLIST" not in combined
    assert "ADM_USER_GLOBAL_BLOCK" not in combined
    assert "ADM_USER_GLOBAL_UNBLOCK" not in combined

    assert (
        "def open_super_admin_global_blacklist"
        in Path(
            "handlers/"
            "super_admin_global_blacklist.py"
        ).read_text(encoding="utf-8")
    )
    assert (
        "SA_GLOBAL_BLACKLIST"
        in Path(
            "handlers/"
            "super_admin_global_blacklist.py"
        ).read_text(encoding="utf-8")
    )

@pytest.mark.parametrize(
    "language",
    ("ru", "en", "pt", "uk", "pl", "de", "nl"),
)
def test_super_admin_menu_shows_global_blacklist(
    language,
):
    summary = SuperAdminMenuSummary(
        users=419,
        professional_cabinets=192,
        tickets=30,
        complaints=12,
        global_blacklist=2,
        system_alerts=0,
        finance_alerts=0,
        audit_alerts=295,
    )

    keyboard = super_admin_menu_keyboard(
        summary,
        language,
        show_role_switch=False,
    )
    buttons = [
        button
        for row in keyboard.inline_keyboard
        for button in row
    ]
    callbacks = {
        button.callback_data
        for button in buttons
        if button.callback_data
    }

    assert "SA_GLOBAL_BLACKLIST" in callbacks
    assert "ADM_GLOBAL_BLACKLIST" not in callbacks

    global_button = next(
        button
        for button in buttons
        if button.callback_data
        == "SA_GLOBAL_BLACKLIST"
    )
    assert "2" in global_button.text
    assert global_button.text.startswith("⛔ ")

