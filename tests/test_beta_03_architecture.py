from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_legal_handler_uses_services_and_i18n_not_direct_db_logic():
    source = read("handlers/legal.py")

    assert "from sqlalchemy" not in source
    assert "UserRepository" not in source
    assert "from database.models import User" not in source
    assert "UserService" in source
    assert "LegalService" in source
    assert "t(" in source

    forbidden_text_fragments = [
        "Принять",
        "Юридические",
        "Согласия",
        "Главное меню",
        "Назад",
        "регистрац",
    ]

    for fragment in forbidden_text_fragments:
        assert fragment not in source


def test_legal_i18n_keys_exist():
    source = read("ui/texts.py")

    required_keys = [
        "legal_show_documents_btn",
        "legal_accept_continue_btn",
        "legal_back_to_menu_btn",
        "legal_continue_specialist_registration_btn",
        "legal_gate_intro",
        "legal_gate_required_docs",
        "legal_gate_confirmation",
        "legal_start_required",
        "legal_documents_not_configured",
        "legal_already_accepted",
        "legal_accept_failed",
        "legal_accepted",
        "legal_main_menu",
    ]

    for key in required_keys:
        assert key in source

def test_legal_gate_has_show_documents_callback():
    source = read("handlers/legal.py")

    assert "CB_LEGAL_SHOW_DOCS" in source
    assert "LEGAL_SHOW_DOCS" in source
    assert "legal_show_documents_btn" in source
    assert "show_specialist_legal_documents" in source

def test_disabled_beta_sections_use_single_placeholder_contract():
    admin_source = read("handlers/admin.py")
    billing_source = read("handlers/billing.py")
    texts_source = read("ui/texts.py")

    assert '"feature_disabled_beta"' in texts_source
    assert '"feature_disabled_beta_message"' in texts_source

    assert 'callback_data="ADMIN_BETA_DISABLED:finance"' in admin_source
    assert 'callback_data="BETA_DISABLED:promotion"' in billing_source

    assert "async def show_admin_beta_disabled_feature" in admin_source
    assert "async def beta_disabled" in billing_source

    assert 't("feature_disabled_beta", language)' in admin_source
    assert 't("feature_disabled_beta", language)' in billing_source

    assert 't("feature_disabled_beta_message", language)' in admin_source
    assert 't("feature_disabled_beta_message", language)' in billing_source

def test_auto_translate_default_is_disabled_for_controlled_beta():
    source = read("database/models.py")

    assert (
        "auto_translate_enabled: Mapped[bool] = mapped_column(Boolean, default=False)"
        in source
    )

def test_acceptance_stale_callbacks_and_critical_edits_are_guarded():
    billing_source = read("handlers/billing.py")
    texts_source = read("ui/texts.py")

    assert "async def block_critical_profile_edit(" in billing_source
    assert "async def block_critical_profile_edit_message(" in billing_source
    assert "block_stale_critical_profile_edit_callbacks" in billing_source
    assert "critical_profile_change_requires_pending_schema" in billing_source
    assert "cabinet_critical_edit_blocked" in billing_source
    assert '"cabinet_critical_edit_blocked"' in texts_source

    assert "except (IndexError, TypeError, ValueError)" in billing_source
    assert "show_alert=True" in billing_source
    assert "contact_request_not_found" in billing_source
    assert "contact_thread_not_found" in billing_source

def test_acceptance_external_payment_webhook_is_not_enabled_for_controlled_beta():
    billing_handler_source = open("handlers/billing.py", encoding="utf-8").read()
    billing_repository_source = open("database/repositories/billing.py", encoding="utf-8").read()

    assert "@billing_router.post" not in billing_handler_source
    assert "external_payment_id" not in billing_handler_source
    assert "provider_payment_id=None" in billing_repository_source
    assert "Payment.status.in_([\"pending\", \"paid\"])" in billing_repository_source