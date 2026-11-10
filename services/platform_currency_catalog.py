"""Shared offline currency metadata from the catalog already used by Platform."""
from dataclasses import dataclass
import pycountry


class PlatformCurrencyCodeError(ValueError):
    pass


@dataclass(frozen=True)
class PlatformCurrencyDefinition:
    code: str
    name: str


def require_platform_currency(code):
    if (
        not isinstance(code, str)
        or len(code) != 3
        or not code.isascii()
        or not code.isalpha()
        or code != code.upper()
    ):
        raise PlatformCurrencyCodeError("Currency code is invalid.")

    row = pycountry.currencies.get(alpha_3=code)
    if row is None:
        raise PlatformCurrencyCodeError("Currency code is unknown.")

    name = getattr(row, "name", None)
    if (
        getattr(row, "alpha_3", None) != code
        or not isinstance(name, str)
        or not name.strip()
    ):
        raise RuntimeError("Platform currency catalog is invalid.")

    return PlatformCurrencyDefinition(code, name)


def list_platform_currencies():
    return tuple(sorted(
        (require_platform_currency(row.alpha_3) for row in pycountry.currencies),
        key=lambda row: row.code,
    ))

# con_012_currency_settings_v1
