"""Bounded tax-rule positions, not authorization credentials."""
import base64
import json
import re
from uuid import UUID

from services.construction.construction_tax_rules import (
    ConstructionTaxRuleCursor, ConstructionTaxRuleValidationError,
)

MAX_TAX_RULE_CURSOR_LENGTH = 1024
KIND = "construction.tax_rules.list"
FIELDS = frozenset({"v", "kind", "tenant_id", "user_id", "profile_id", "id"})


def _json(value):
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")


def _base64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate cursor field.")
        result[key] = value
    return result


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("Canonical UUID required.")
    result = UUID(value)
    if str(result) != value:
        raise ValueError("Canonical UUID required.")
    return result


def _context(tenant_id, user_id, profile_id):
    if not all(isinstance(value, UUID) for value in (tenant_id, user_id, profile_id)):
        raise ValueError("UUID context required.")


def encode_tax_rule_cursor(cursor, *, tenant_id, user_id, profile_id):
    try:
        _context(tenant_id, user_id, profile_id)
        if cursor is None:
            return None
        if not isinstance(cursor, ConstructionTaxRuleCursor):
            raise ValueError("Tax rule cursor required.")
        cursor = ConstructionTaxRuleCursor(
            cursor.tenant_id, cursor.user_id, cursor.tax_profile_id, cursor.after_id,
        )
        if (cursor.tenant_id, cursor.user_id, cursor.tax_profile_id) != (tenant_id, user_id, profile_id):
            raise ValueError("Cursor context differs.")
        value = _base64(_json({
            "v": 1, "kind": KIND, "tenant_id": str(tenant_id), "user_id": str(user_id),
            "profile_id": str(profile_id), "id": str(cursor.after_id),
        }))
        if len(value) > MAX_TAX_RULE_CURSOR_LENGTH:
            raise ValueError("Cursor too long.")
        return value
    except Exception:
        raise ConstructionTaxRuleValidationError("Tax rule cursor is invalid.") from None


def decode_tax_rule_cursor(value, *, tenant_id, user_id, profile_id):
    try:
        _context(tenant_id, user_id, profile_id)
        if value is None:
            return None
        if (not isinstance(value, str) or not 1 <= len(value) <= MAX_TAX_RULE_CURSOR_LENGTH
                or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None):
            raise ValueError("Invalid cursor encoding.")
        raw = base64.b64decode(
            (value + "=" * ((-len(value)) % 4)).encode("ascii"), altchars=b"-_", validate=True,
        )
        if _base64(raw) != value:
            raise ValueError("Noncanonical cursor encoding.")
        record = json.loads(raw.decode("ascii"), object_pairs_hook=_object)
        if (not isinstance(record, dict) or set(record) != FIELDS
                or type(record["v"]) is not int or record["v"] != 1
                or record["kind"] != KIND or _json(record) != raw):
            raise ValueError("Invalid cursor record.")
        if (_uuid(record["tenant_id"]), _uuid(record["user_id"]), _uuid(record["profile_id"])) != (
            tenant_id, user_id, profile_id,
        ):
            raise ValueError("Cursor belongs to another context.")
        return ConstructionTaxRuleCursor(tenant_id, user_id, profile_id, _uuid(record["id"]))
    except Exception:
        raise ConstructionTaxRuleValidationError("Tax rule cursor is invalid.") from None
