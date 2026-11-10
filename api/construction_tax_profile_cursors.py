"""Bounded tax-profile positions; every request still requires fresh authorization."""
import base64
import json
import re
from uuid import UUID

from services.construction.construction_tax_profiles import (
    ConstructionTaxProfileCursor, ConstructionTaxProfileValidationError,
)

MAX_TAX_PROFILE_CURSOR_LENGTH = 512
KIND = "construction.tax_profiles.list"
FIELDS = {"v", "kind", "tenant_id", "user_id", "id"}


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def _base64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _uuid(value):
    if type(value) is not str:
        raise ValueError("Canonical UUID required.")
    result = UUID(value)
    if str(result) != value:
        raise ValueError("Canonical UUID required.")
    return result


def encode_tax_profile_cursor(cursor, *, tenant_id, user_id):
    try:
        if not isinstance(tenant_id, UUID) or not isinstance(user_id, UUID):
            raise ValueError("Canonical context required.")
        if cursor is None:
            return None
        if not isinstance(cursor, ConstructionTaxProfileCursor):
            raise ValueError("Tax profile cursor required.")
        cursor = ConstructionTaxProfileCursor(cursor.tenant_id, cursor.user_id, cursor.after_id)
        if (cursor.tenant_id, cursor.user_id) != (tenant_id, user_id):
            raise ValueError("Cursor context differs.")
        value = _base64(_json(dict(
            v=1, kind=KIND, tenant_id=str(tenant_id), user_id=str(user_id), id=str(cursor.after_id))))
        if len(value) > MAX_TAX_PROFILE_CURSOR_LENGTH:
            raise ValueError("Cursor too long.")
        return value
    except Exception:
        raise ConstructionTaxProfileValidationError("Tax profile cursor is invalid.") from None


def decode_tax_profile_cursor(value, *, tenant_id, user_id):
    try:
        if not isinstance(tenant_id, UUID) or not isinstance(user_id, UUID):
            raise ValueError("Canonical context required.")
        if value is None:
            return None
        if (type(value) is not str or not 1 <= len(value) <= MAX_TAX_PROFILE_CURSOR_LENGTH
                or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None):
            raise ValueError("Invalid cursor encoding.")
        raw = base64.b64decode(
            (value + "=" * ((-len(value)) % 4)).encode("ascii"), altchars=b"-_", validate=True)
        if _base64(raw) != value:
            raise ValueError("Noncanonical cursor encoding.")
        record = json.loads(raw.decode("ascii"))
        if (not isinstance(record, dict) or set(record) != FIELDS or type(record["v"]) is not int
                or record["v"] != 1 or record["kind"] != KIND or _json(record) != raw):
            raise ValueError("Invalid cursor record.")
        if (_uuid(record["tenant_id"]), _uuid(record["user_id"])) != (tenant_id, user_id):
            raise ValueError("Cursor belongs to another context.")
        return ConstructionTaxProfileCursor(tenant_id, user_id, _uuid(record["id"]))
    except Exception:
        raise ConstructionTaxProfileValidationError("Tax profile cursor is invalid.") from None
