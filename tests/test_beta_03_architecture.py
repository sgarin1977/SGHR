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