import pytest

from services.platform_company_bank_accounts import (
    CompanyBankAccountValidationError,
    normalize_company_bank_account_fields,
)


def bank_fields(**overrides):
    values = {
        "account_holder_name": "Example Legal Ltd",
        "country_code": "PT",
        "iban": "PT50 0002 0123 1234 5678 9015 4",
        "bic_swift": " cgdiptpl ",
        "account_number": None,
        "currency": " eur ",
    }
    return normalize_company_bank_account_fields(
        **(values | overrides)
    )


def test_bank_account_normalizes_iban_bic_and_currency():
    result = bank_fields()
    assert result["iban"] == "PT50000201231234567890154"
    assert result["bic_swift"] == "CGDIPTPL"
    assert result["currency"] == "EUR"


@pytest.mark.parametrize(
    "iban,bic_swift,currency",
    [
        ("PT50000201231234567890155", "CGDIPTPL", "EUR"),
        ("PT00", "CGDIPTPL", "EUR"),
        ("PT50000201231234567890154", "INVALID!", "EUR"),
        ("PT50000201231234567890154", "CGDIPTPL", "ZZZ"),
    ],
)
def test_bank_account_rejects_invalid_codes(
    iban, bic_swift, currency,
):
    with pytest.raises(CompanyBankAccountValidationError):
        bank_fields(
            iban=iban, bic_swift=bic_swift, currency=currency,
        )


def test_bank_account_requires_iban_or_account_number():
    with pytest.raises(CompanyBankAccountValidationError):
        bank_fields(iban=None, account_number=None)


def test_bank_account_accepts_local_account_number_only():
    result = bank_fields(
        iban=None, bic_swift=None, account_number="123456",
    )
    assert result["iban"] is None
    assert result["account_number"] == "123456"
