"""Bounded project-activity positions; every request still requires authorization."""
import base64
import binascii
from dataclasses import replace
from datetime import datetime, timezone
import json
import re
from uuid import UUID

from services.construction.construction_activity_read import ConstructionActivityReadCursor
from services.construction.construction_activity_rules import ConstructionActivityValidationError

MAX_CURSOR_LENGTH = 1024
KIND = "construction.project.activities"
FIELDS = {"v", "kind", "tenant", "user", "project", "at", "id"}


def error():
    return ConstructionActivityValidationError("Project activity cursor is invalid.")


def context(tenant_id, user_id, project_id):
    if any(not isinstance(value, UUID) for value in (tenant_id, user_id, project_id)):
        raise error()


def instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise error()
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def uuid(value):
    if not isinstance(value, str):
        raise error()
    parsed = UUID(value)
    if str(parsed) != value:
        raise error()
    return parsed


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise error()
        result[key] = value
    return result


def encode_cursor(cursor, *, tenant_id, user_id, project_id):
    context(tenant_id, user_id, project_id)
    if cursor is None:
        return None
    try:
        if not isinstance(cursor, ConstructionActivityReadCursor):
            raise error()
        cursor = replace(cursor)
        if (
            cursor.tenant_id != tenant_id or cursor.user_id != user_id
            or cursor.filters != (None, None, project_id, None)
        ):
            raise error()
        token = b64(dump({
            "v": 1, "kind": KIND, "tenant": str(tenant_id), "user": str(user_id),
            "project": str(project_id), "at": instant(cursor.due_at),
            "id": str(cursor.activity_id),
        }))
        if len(token) > MAX_CURSOR_LENGTH:
            raise error()
        return token
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise error() from None


def decode_cursor(token, *, tenant_id, user_id, project_id):
    context(tenant_id, user_id, project_id)
    if token is None:
        return None
    try:
        if (
            not isinstance(token, str) or not 1 <= len(token) <= MAX_CURSOR_LENGTH
            or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None
        ):
            raise error()
        raw = base64.b64decode(
            (token + "=" * ((-len(token)) % 4)).encode("ascii"),
            altchars=b"-_", validate=True,
        )
        value = json.loads(raw.decode("ascii"), object_pairs_hook=object_pairs)
        if (
            b64(raw) != token or not isinstance(value, dict) or set(value) != FIELDS
            or type(value["v"]) is not int or value["v"] != 1 or value["kind"] != KIND
            or dump(value) != raw
            or (uuid(value["tenant"]), uuid(value["user"]), uuid(value["project"]))
               != (tenant_id, user_id, project_id)
            or not isinstance(value["at"], str)
        ):
            raise error()
        at = datetime.fromisoformat(value["at"].replace("Z", "+00:00"))
        if instant(at) != value["at"]:
            raise error()
        return ConstructionActivityReadCursor(
            tenant_id, user_id, (None, None, project_id, None), at, uuid(value["id"]),
        )
    except (
        ValueError, TypeError, KeyError, AttributeError, OverflowError,
        UnicodeError, RecursionError, binascii.Error,
    ):
        raise error() from None
