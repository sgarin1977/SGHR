"""Opaque CRM lead positions, following the existing client-card cursor pattern.

This is a bounded position encoding, not a signature or permission credential.
The list service must still authenticate, authorize and scope every request.
No public route, response envelope or filter requiredness is registered here.
"""
import base64
import binascii
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID

from services.construction_lead_list_read import ConstructionLeadListFilters, ConstructionLeadListReadCursor
from services.construction_lead_rules import ConstructionLeadValidationError

MAX_LEAD_LIST_CURSOR_LENGTH = 1024
_KIND = 'construction.crm.leads'
_FIELDS = frozenset({'v','kind','tenant_id','user_id','filters_hash','at','id'})


def _error():
    return ConstructionLeadValidationError('Lead list cursor is invalid.')


def _json(value):
    return json.dumps(value,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(',',':')).encode('ascii')


def _base64(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _uuid(value):
    if not isinstance(value,str):
        raise _error()
    checked = UUID(value)
    if str(checked)!=value:
        raise _error()
    return checked


def _instant(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _error()
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def _object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise _error()
        result[key] = value
    return result


def _context(tenant_id,user_id,filters):
    if not isinstance(tenant_id,UUID) or not isinstance(user_id,UUID):
        raise _error()
    if filters is not None and not isinstance(filters,ConstructionLeadListFilters):
        raise _error()
    try:
        normalized = ConstructionLeadListFilters(**filters.values()) if filters is not None else ConstructionLeadListFilters()
    except ConstructionLeadValidationError:
        raise _error() from None
    values = {name:str(value) if isinstance(value,UUID) else value for name,value in normalized.values().items()}
    # Fixed-width digest keeps cursor size bounded without introducing a new
    # business limit for extensible source codes. It is not an authentication MAC.
    return normalized,hashlib.sha256(_json(values)).hexdigest()


def encode_lead_list_cursor(cursor, *, tenant_id, user_id, filters=None):
    try:
        normalized,digest = _context(tenant_id,user_id,filters)
        if cursor is None:
            return None
        if not isinstance(cursor,ConstructionLeadListReadCursor):
            raise _error()
        cursor = replace(cursor)
        if (cursor.tenant_id,cursor.user_id,cursor.filters)!=(tenant_id,user_id,normalized):
            raise _error()
        record = dict(v=1,kind=_KIND,tenant_id=str(tenant_id),user_id=str(user_id),
            filters_hash=digest,at=_instant(cursor.updated_at),id=str(cursor.lead_id))
        encoded = _base64(_json(record))
        if len(encoded)>MAX_LEAD_LIST_CURSOR_LENGTH:
            raise _error()
        return encoded
    except ConstructionLeadValidationError:
        raise _error() from None
    except (ValueError,TypeError,AttributeError,OverflowError,UnicodeError):
        raise _error() from None


def decode_lead_list_cursor(encoded, *, tenant_id, user_id, filters=None):
    try:
        normalized,digest = _context(tenant_id,user_id,filters)
        if encoded is None:
            return None
        if (not isinstance(encoded,str) or not 1<=len(encoded)<=MAX_LEAD_LIST_CURSOR_LENGTH
                or re.fullmatch(r'[A-Za-z0-9_-]+',encoded) is None):
            raise _error()
        raw = base64.b64decode((encoded+'='*((-len(encoded))%4)).encode('ascii'),altchars=b'-_',validate=True)
        if _base64(raw)!=encoded:
            raise _error()
        record = json.loads(raw.decode('ascii'),object_pairs_hook=_object)
        if (not isinstance(record,dict) or set(record)!=_FIELDS or type(record['v']) is not int
                or record['v']!=1 or record['kind']!=_KIND or _json(record)!=raw):
            raise _error()
        if (_uuid(record['tenant_id']),_uuid(record['user_id']))!=(tenant_id,user_id):
            raise _error()
        if record['filters_hash']!=digest or not isinstance(record['at'],str):
            raise _error()
        instant = datetime.fromisoformat(record['at'].replace('Z','+00:00'))
        if _instant(instant)!=record['at']:
            raise _error()
        return ConstructionLeadListReadCursor(tenant_id,user_id,normalized,instant,_uuid(record['id']))
    except ConstructionLeadValidationError:
        raise _error() from None
    except (ValueError,TypeError,AttributeError,KeyError,OverflowError,UnicodeError,RecursionError,binascii.Error):
        raise _error() from None
