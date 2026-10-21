"""Opaque client-card positions, not authorization or integrity credentials."""
import base64
import binascii
from dataclasses import replace
from datetime import datetime, timezone
import json
import re
from uuid import UUID

from services.construction_client_projects_read import ConstructionClientProjectsReadCursor
from services.construction_client_leads_read import ConstructionClientLeadsReadCursor
from services.construction_client_activities_read import ConstructionClientActivitiesReadCursor
from services.construction_client_history_read import ConstructionClientHistoryReadCursor
from services.construction_clients import ConstructionClientValidationError


MAX_CLIENT_CARD_CURSOR_LENGTH = 1024
_CURSORS = {
    'projects': (ConstructionClientProjectsReadCursor, 'updated_at', 'project_id'),
    'leads': (ConstructionClientLeadsReadCursor, 'updated_at', 'lead_id'),
    'activities': (ConstructionClientActivitiesReadCursor, 'due_at', 'activity_id'),
    'history': (ConstructionClientHistoryReadCursor, 'created_at', 'event_id'),
}
_FIELDS = frozenset({'v', 'section', 'tenant_id', 'user_id', 'client_id', 'at', 'id'})


def _error():
    return ConstructionClientValidationError('Client card cursor is invalid.')


def _context(section, tenant_id, user_id, client_id):
    if (not isinstance(section, str) or section not in _CURSORS
        or any(not isinstance(value, UUID) for value in (tenant_id, user_id, client_id))):
        raise _error()
    return _CURSORS[section]


def _instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _error()
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _json(record):
    return json.dumps(record, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')


def _base64(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _uuid(value):
    if not isinstance(value, str):
        raise _error()
    checked = UUID(value)
    if str(checked) != value:
        raise _error()
    return checked


def _object(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise _error()
        record[key] = value
    return record


def encode_client_card_cursor(cursor, *, section, tenant_id, user_id, client_id):
    """Serialize an existing service cursor without adding claims or secrets."""
    try:
        cursor_type, time_field, id_field = _context(section, tenant_id, user_id, client_id)
        if cursor is None:
            return None
        if not isinstance(cursor, cursor_type):
            raise _error()
        cursor = replace(cursor)
        if (cursor.tenant_id, cursor.user_id, cursor.client_id) != (tenant_id, user_id, client_id):
            raise _error()
        identifier = getattr(cursor, id_field)
        if not isinstance(identifier, UUID):
            raise _error()
        record = dict(v=1, section=section, tenant_id=str(tenant_id), user_id=str(user_id),
            client_id=str(client_id), at=_instant(getattr(cursor, time_field)), id=str(identifier))
        token = _base64(_json(record))
        if len(token) > MAX_CLIENT_CARD_CURSOR_LENGTH:
            raise _error()
        return token
    except ConstructionClientValidationError:
        raise
    except (ValueError, TypeError, AttributeError, OverflowError, UnicodeError):
        raise _error() from None


def decode_client_card_cursor(token, *, section, tenant_id, user_id, client_id):
    """Decode a position only; caller must still authorize and scope every read."""
    try:
        cursor_type, time_field, id_field = _context(section, tenant_id, user_id, client_id)
        if token is None:
            return None
        if (not isinstance(token, str) or not 1 <= len(token) <= MAX_CLIENT_CARD_CURSOR_LENGTH
            or re.fullmatch(r'[A-Za-z0-9_-]+', token) is None):
            raise _error()
        raw = base64.b64decode((token+'='*((-len(token)) % 4)).encode('ascii'), altchars=b'-_', validate=True)
        if _base64(raw) != token:
            raise _error()
        record = json.loads(raw.decode('ascii'), object_pairs_hook=_object)
        if (not isinstance(record, dict) or set(record) != _FIELDS or type(record['v']) is not int
            or record['v'] != 1 or record['section'] != section or _json(record) != raw):
            raise _error()
        if (_uuid(record['tenant_id']), _uuid(record['user_id']), _uuid(record['client_id'])) != (tenant_id, user_id, client_id):
            raise _error()
        if not isinstance(record['at'], str):
            raise _error()
        instant = datetime.fromisoformat(record['at'].replace('Z', '+00:00'))
        if _instant(instant) != record['at']:
            raise _error()
        return cursor_type(tenant_id=tenant_id, user_id=user_id, client_id=client_id,
            **{time_field: instant, id_field: _uuid(record['id'])})
    except ConstructionClientValidationError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError, UnicodeError, RecursionError, binascii.Error):
        raise _error() from None
