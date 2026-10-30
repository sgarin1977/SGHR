import pytest


def test_con_c001_identifier_normalization_preserves_original_value():
    from services.platform_company_identifiers import (
        normalize_company_identifier_fields,
    )

    raw_value = "  pt 123-45 / a.\t"
    assert normalize_company_identifier_fields(
        identifier_type="  VAT  ",
        identifier_value=raw_value,
    ) == {
        "identifier_type": "vat",
        "identifier_value": raw_value,
        "identifier_value_normalized": "PT123-45/A.",
    }


@pytest.mark.parametrize(
    "value",
    ("", "   ", "\t\n"),
)
def test_con_c001_identifier_rejects_blank_value(value):
    from services.platform_company_identifiers import (
        CompanyIdentifierValidationError,
        normalize_company_identifier_fields,
    )

    with pytest.raises(CompanyIdentifierValidationError):
        normalize_company_identifier_fields(
            identifier_type="vat",
            identifier_value=value,
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" pt ", "PT"), ("UA", "UA")],
)
def test_con_c001_identifier_country_uses_iso_alpha2(raw, expected):
    from services.platform_company_identifiers import (
        normalize_company_identifier_country_code,
    )

    assert normalize_company_identifier_country_code(raw) == expected


@pytest.mark.parametrize("value", ("ZZ", "P", "USA", ""))
def test_con_c001_identifier_rejects_non_iso_country(value):
    from services.platform_company_identifiers import (
        CompanyIdentifierValidationError,
        normalize_company_identifier_country_code,
    )

    with pytest.raises(CompanyIdentifierValidationError):
        normalize_company_identifier_country_code(value)
