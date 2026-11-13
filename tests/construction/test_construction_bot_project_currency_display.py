"""The project card displays its stored currency, never an organization default."""
from copy import deepcopy
from uuid import uuid4

import pytest

from ui.construction_project_card import (
    LANGUAGES, LABELS, project_text, tr,
)


@pytest.fixture
def result():
    return {
        "actor_user_id": str(uuid4()),
        "organization": "Organization",
        "organization_default_currency": "USD",
        "currency": "USD",
        "item": {
            "name": "Project",
            "status": "draft",
            "responsible_user_id": None,
            "address_raw": "Original address",
            "address_formatted": None,
            "address_verification_status": "pending",
            "comment": "Original comment",
            "created_at": "2026-11-12T10:00:00+00:00",
            "updated_at": "2026-11-12T10:00:00+00:00",
            "currency": "EUR",
        },
    }


def currency_line(value, language):
    prefix = tr("currency", language) + ": "
    return next(line for line in project_text(value, language).splitlines() if line.startswith(prefix))


@pytest.mark.parametrize("language", LANGUAGES)
def test_project_currency_survives_organization_default_change(result, language):
    before = deepcopy(result)
    original = currency_line(result, language)
    assert original == tr("currency", language) + ": EUR"
    result["organization_default_currency"] = "UAH"
    result["currency"] = "UAH"
    assert currency_line(result, language) == original
    assert result["item"] == before["item"]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("legacy", ["null", "missing"])
def test_unknown_legacy_currency_is_not_replaced_with_default(result, language, legacy):
    if legacy == "missing":
        result["item"].pop("currency")
    else:
        result["item"]["currency"] = None
    before = deepcopy(result)
    assert currency_line(result, language) == (
        tr("currency", language) + ": " + tr("currency_unknown", language)
    )
    assert result == before


@pytest.mark.parametrize("currency", ["UAH", "EUR", "USD", "JPY"])
def test_display_uses_server_value_without_changing_project_data(result, currency):
    result["item"]["currency"] = currency
    before = deepcopy(result)
    assert currency_line(result, "uk") == tr("currency", "uk") + ": " + currency
    assert result == before


@pytest.mark.parametrize("currency", ["eur", "EURO", "<b>", "", 123, True])
def test_malformed_currency_cannot_be_presented_as_canonical(result, currency):
    result["item"]["currency"] = currency
    with pytest.raises(ValueError):
        project_text(result, "uk")


def test_currency_labels_cover_all_interface_languages():
    for key in ("currency", "currency_unknown"):
        assert set(LABELS[key]) == set(LANGUAGES)
        assert all(LABELS[key].values())
