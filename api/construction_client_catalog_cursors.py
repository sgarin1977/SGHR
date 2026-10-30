"""Bounded client-list positions; each request still requires fresh access."""
import base64
from dataclasses import replace
from datetime import datetime, timezone
import json
import re
from uuid import UUID

from services.construction.construction_client_catalog_read import ConstructionClientCatalogReadCursor
from services.construction.construction_clients import ConstructionClientValidationError

MAX_CLIENT_CATALOG_CURSOR_LENGTH = 1024
KIND = "construction.clients.catalog"
FIELDS = frozenset({"v", "kind", "tenant_id", "user_id", "at", "id"})


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


def _instant(value):
    if (
        not isinstance(value, datetime) or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Aware timestamp required.")
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds",
    ).replace("+00:00", "Z")


def encode_client_catalog_cursor(cursor, *, tenant_id, user_id):
    try:
        if not isinstance(tenant_id, UUID) or not isinstance(user_id, UUID):
            raise ValueError("UUID context required.")
        if cursor is None:
            return None
        if not isinstance(cursor, ConstructionClientCatalogReadCursor):
            raise ValueError("Client cursor required.")
        cursor = replace(cursor)
        if (cursor.tenant_id, cursor.user_id) != (tenant_id, user_id):
            raise ValueError("Cursor context differs.")
        value = _base64(_json({
            "v": 1, "kind": KIND, "tenant_id": str(tenant_id), "user_id": str(user_id),
            "at": _instant(cursor.updated_at), "id": str(cursor.client_id),
        }))
        if len(value) > MAX_CLIENT_CATALOG_CURSOR_LENGTH:
            raise ValueError("Cursor too long.")
        return value
    except Exception:
        raise ConstructionClientValidationError("Client catalog cursor is invalid.") from None


def decode_client_catalog_cursor(value, *, tenant_id, user_id):
    try:
        if not isinstance(tenant_id, UUID) or not isinstance(user_id, UUID):
            raise ValueError("UUID context required.")
        if value is None:
            return None
        if (
            not isinstance(value, str) or not 1 <= len(value) <= MAX_CLIENT_CATALOG_CURSOR_LENGTH
            or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
        ):
            raise ValueError("Invalid cursor encoding.")
        raw = base64.b64decode(
            (value + "=" * ((-len(value)) % 4)).encode("ascii"),
            altchars=b"-_", validate=True,
        )
        if _base64(raw) != value:
            raise ValueError("Noncanonical cursor.")
        record = json.loads(raw.decode("ascii"), object_pairs_hook=_object)
        if (
            not isinstance(record, dict) or set(record) != FIELDS
            or type(record["v"]) is not int or record["v"] != 1
            or record["kind"] != KIND or _json(record) != raw
        ):
            raise ValueError("Invalid cursor record.")
        if (
            _uuid(record["tenant_id"]), _uuid(record["user_id"])
        ) != (tenant_id, user_id):
            raise ValueError("Cursor context differs.")
        if not isinstance(record["at"], str):
            raise ValueError("Invalid cursor timestamp.")
        instant = datetime.fromisoformat(record["at"].replace("Z", "+00:00"))
        if _instant(instant) != record["at"]:
            raise ValueError("Noncanonical cursor timestamp.")
        return ConstructionClientCatalogReadCursor(
            tenant_id, user_id, instant, _uuid(record["id"]),
        )
    except Exception:
        raise ConstructionClientValidationError("Client catalog cursor is invalid.") from None
