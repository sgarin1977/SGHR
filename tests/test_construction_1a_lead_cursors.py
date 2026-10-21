import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import pytest

from api.construction_lead_cursors import (
    MAX_LEAD_LIST_CURSOR_LENGTH, decode_lead_list_cursor, encode_lead_list_cursor,
)
from services.construction_lead_list_read import ConstructionLeadListFilters, ConstructionLeadListReadCursor
from services.construction_lead_rules import ConstructionLeadValidationError
from services.construction_permissions import ConstructionPermissionDeniedError
from test_construction_1a_lead_list_read import list_case, listing, another
from test_construction_1a_lead_detail_read import detail_case, no_writes
from test_construction_1a_activity_read_service import read_case
from test_construction_1a_activity_terminal_service import terminal_case


TENANT, USER, LEAD = UUID(int=1), UUID(int=2), UUID(int=3)
NOW = datetime(2026, 10, 19, 12, 34, 56, 123456, tzinfo=timezone.utc)


def context(**changes):
    return dict(tenant_id=TENANT, user_id=USER, filters=ConstructionLeadListFilters(), **changes)


def cursor(filters=None, instant=NOW):
    return ConstructionLeadListReadCursor(TENANT, USER, filters or ConstructionLeadListFilters(), instant, LEAD)


def encoded_record(record, *, canonical=True):
    raw = json.dumps(record, sort_keys=canonical, separators=(',', ':') if canonical else None).encode('ascii')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def unpack(encoded):
    return json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))


def invalid(call):
    with pytest.raises(ConstructionLeadValidationError, match=r'^Lead list cursor is invalid\.$'):
        call()


@pytest.mark.parametrize('offset', [-12, -3, 0, 2, 14])
def test_round_trip_normalizes_utc_and_preserves_microseconds(offset):
    original = cursor(instant=NOW.astimezone(timezone(timedelta(hours=offset))))
    encoded = encode_lead_list_cursor(original, **context())
    assert len(encoded) < MAX_LEAD_LIST_CURSOR_LENGTH and '=' not in encoded
    assert unpack(encoded)['at'] == '2026-10-19T12:34:56.123456Z'
    assert decode_lead_list_cursor(encoded, **context()) == original
    assert encode_lead_list_cursor(decode_lead_list_cursor(encoded, **context()), **context()) == encoded


def test_normalized_filter_context_round_trip_and_fixed_size_digest():
    filters = ConstructionLeadListFilters('new', ' PHONE ', UUID(int=4), UUID(int=5), UUID(int=6))
    kwargs = context(); kwargs['filters'] = filters
    encoded = encode_lead_list_cursor(cursor(filters), **kwargs)
    kwargs['filters'] = replace(filters, source='phone')
    assert decode_lead_list_cursor(encoded, **kwargs) == cursor(filters)
    record = unpack(encoded)
    assert len(record['filters_hash']) == 64 and 'phone' not in json.dumps(record)


def test_extensible_source_does_not_introduce_a_business_length_limit():
    filters = ConstructionLeadListFilters(source='джерело_' * 10000)
    kwargs = context(); kwargs['filters'] = filters
    encoded = encode_lead_list_cursor(cursor(filters), **kwargs)
    assert len(encoded) < MAX_LEAD_LIST_CURSOR_LENGTH
    assert decode_lead_list_cursor(encoded, **kwargs).filters == filters


def test_no_next_page_has_no_cursor():
    assert encode_lead_list_cursor(None, **context()) is None
    assert decode_lead_list_cursor(None, **context()) is None


@pytest.mark.parametrize('field', ['tenant_id', 'user_id'])
def test_cross_context_cursor_cannot_be_reused(field):
    encoded = encode_lead_list_cursor(cursor(), **context())
    kwargs = context(); kwargs[field] = uuid4()
    invalid(lambda: decode_lead_list_cursor(encoded, **kwargs))
    invalid(lambda: encode_lead_list_cursor(cursor(), **kwargs))


@pytest.mark.parametrize('changes', [
    {'status': 'contacted'}, {'source': 'phone'}, {'responsible_user_id': UUID(int=4)},
    {'converted_client_id': UUID(int=5)}, {'converted_project_id': UUID(int=6)},
])
def test_any_filter_change_requires_a_new_position(changes):
    encoded = encode_lead_list_cursor(cursor(), **context())
    kwargs = context(); kwargs['filters'] = ConstructionLeadListFilters(**changes)
    invalid(lambda: decode_lead_list_cursor(encoded, **kwargs))
    invalid(lambda: encode_lead_list_cursor(cursor(), **kwargs))


@pytest.mark.parametrize('bad', ['', ' ', '=', 'a', 'abc=', 'abc/', 'abc+', 'é', 'x' * 1025, 1, True, [], {}])
def test_invalid_encoding_is_bounded_and_sanitized(bad):
    invalid(lambda: decode_lead_list_cursor(bad, **context()))


@pytest.mark.parametrize('field,value', [
    ('v', 2), ('v', True), ('v', 1.0), ('kind', 'construction.clients'), ('kind', None),
    ('tenant_id', str(uuid4())), ('tenant_id', TENANT.hex), ('user_id', str(uuid4())),
    ('id', LEAD.hex), ('id', None), ('id', 'ABCDEFAB-CDEF-ABCD-EFAB-CDEFABCDEFAB'),
    ('at', '2026-10-19T12:34:56.123456'), ('at', '2026-10-19T12:34:56.123456+00:00'),
    ('at', '2026-10-19T12:34:56Z'), ('at', '2026-02-30T12:34:56.123456Z'), ('at', 1),
    ('filters_hash', '0' * 64), ('filters_hash', None), ('filters_hash', []),
])
def test_noncanonical_or_mismatched_records_are_rejected(field, value):
    record = unpack(encode_lead_list_cursor(cursor(), **context()))
    record[field] = value
    invalid(lambda: decode_lead_list_cursor(encoded_record(record), **context()))


@pytest.mark.parametrize('change', ['extra', 'missing', 'spaces', 'duplicate', 'array', 'null', 'deep'])
def test_exact_canonical_json_shape_is_required(change):
    record = unpack(encode_lead_list_cursor(cursor(), **context()))
    if change == 'extra': record['secret'] = 'do not echo'
    elif change == 'missing': del record['id']
    if change == 'spaces': encoded = encoded_record(record, canonical=False)
    elif change == 'duplicate':
        raw = json.dumps(record, sort_keys=True, separators=(',', ':'))[:-1] + ',"v":1}'
        encoded = base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')
    elif change == 'array': encoded = encoded_record(list(record))
    elif change == 'null': encoded = encoded_record(None)
    elif change == 'deep': encoded = base64.urlsafe_b64encode(('[' * 300 + '0' + ']' * 300).encode()).decode().rstrip('=')
    else: encoded = encoded_record(record)
    invalid(lambda: decode_lead_list_cursor(encoded, **context()))


@pytest.mark.parametrize('change', ['tenant', 'user', 'filters', 'time', 'id', 'type'])
def test_encoder_revalidates_typed_positions(change):
    value = cursor()
    field, bad = {'tenant': ('tenant_id', 'bad'), 'user': ('user_id', 'bad'),
        'filters': ('filters', {}), 'time': ('updated_at', NOW.replace(tzinfo=None)), 'id': ('lead_id', 'bad'),
        'type': ('id', None)}[change]
    if change == 'type': value = {'position': 'not typed'}
    else: object.__setattr__(value, field, bad)
    invalid(lambda: encode_lead_list_cursor(value, **context()))


@pytest.mark.parametrize('change', ['tenant', 'user', 'filters', 'damaged_filters'])
def test_even_empty_page_requires_valid_server_context(change):
    kwargs = context()
    if change == 'tenant': kwargs['tenant_id'] = str(TENANT)
    elif change == 'user': kwargs['user_id'] = None
    elif change == 'filters': kwargs['filters'] = {'source': 'phone'}
    else: object.__setattr__(kwargs['filters'], 'status', 'not-a-status')
    invalid(lambda: encode_lead_list_cursor(None, **kwargs))
    invalid(lambda: decode_lead_list_cursor(None, **kwargs))


@pytest.mark.asyncio
async def test_actual_service_page_can_continue_through_transport(list_case):
    c = list_case
    c.list_repo.list_active_leads.return_value = [c.lead, another(c, 20)]
    page = await listing(c, limit=1)
    kwargs = dict(tenant_id=c.tenant, user_id=c.user)
    encoded = encode_lead_list_cursor(page.next_cursor, **kwargs)
    c.list_repo.list_active_leads.return_value = [another(c, 20)]
    c.list_repo.list_next_actions_for_leads.return_value = []
    final = await listing(c, limit=1, after=decode_lead_list_cursor(encoded, **kwargs))
    assert [item['id'] for item in final.items] == [str(UUID(int=20))]
    assert final.next_cursor is None
    no_writes(c)


@pytest.mark.asyncio
@pytest.mark.parametrize('loss', ['membership', 'grant'])
async def test_cursor_is_a_position_and_never_substitutes_fresh_authorization(list_case, loss):
    c = list_case
    kwargs = dict(tenant_id=c.tenant, user_id=c.user)
    position = ConstructionLeadListReadCursor(c.tenant, c.user, ConstructionLeadListFilters(), c.now, UUID(int=40))
    encoded = encode_lead_list_cursor(position, **kwargs)
    # Position encodings are intentionally unsigned, like existing card cursors.
    # Even a structurally valid edited position cannot authorize a read.
    record = unpack(encoded); record['id'] = str(UUID(int=50))
    after = decode_lead_list_cursor(encoded_record(record), **kwargs)
    if loss == 'membership': c.access.has_active_platform_membership.return_value = False
    else: c.grant.status = 'revoked'
    with pytest.raises(ConstructionPermissionDeniedError): await listing(c, after=after)
    c.list_repo.list_active_leads.assert_not_called()
    no_writes(c)
