import pytest

from handlers.admin import (
    ADMIN_GLOBAL_BLACKLIST_ROLES,
    ADMIN_ROLE_MENU_ROLES,
    format_admin_menu,
    minimal_admin_menu_keyboard,
    moderator_menu_keyboard,
    super_admin_role_scopes_keyboard,
    super_admin_user_roles_keyboard,
)
from services.moderation import (
    AdminMenuSummary,
    ModeratorMenuSummary,
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

