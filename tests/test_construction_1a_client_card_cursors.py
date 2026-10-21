import base64
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import pytest

from api.construction_client_card_cursors import (
    encode_client_card_cursor as encode, decode_client_card_cursor as decode, MAX_CLIENT_CARD_CURSOR_LENGTH,
)
from services.construction_client_projects_read import ConstructionClientProjectsReadCursor
from services.construction_client_leads_read import ConstructionClientLeadsReadCursor
from services.construction_client_activities_read import ConstructionClientActivitiesReadCursor
from services.construction_client_history_read import ConstructionClientHistoryReadCursor
from services.construction_clients import ConstructionClientValidationError


TYPES = dict(projects=ConstructionClientProjectsReadCursor, leads=ConstructionClientLeadsReadCursor,
    activities=ConstructionClientActivitiesReadCursor, history=ConstructionClientHistoryReadCursor)
TENANT, USER, CLIENT, ITEM = [UUID(value+'-1111-4111-8111-111111111111')
    for value in ('aaaaaaaa', 'bbbbbbbb', 'cccccccc', 'dddddddd')]
NOW = datetime(2026, 10, 14, 12, 34, 56, 123456, tzinfo=timezone.utc)


def context(section, **changes):
    return dict(section=section, tenant_id=TENANT, user_id=USER, client_id=CLIENT, **changes)


def make(section, instant=NOW):
    return TYPES[section](TENANT, USER, CLIENT, instant, ITEM)


def raw_token(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def unpack(token):
    return json.loads(base64.urlsafe_b64decode(token+'='*((-len(token)) % 4)))


@pytest.mark.parametrize('section', TYPES)
@pytest.mark.parametrize('offset', [-7, 0, 3])
def test_round_trip_each_existing_cursor_with_exact_utc_microseconds(section, offset):
    original = make(section, NOW.astimezone(timezone(timedelta(hours=offset))))
    token = encode(original, **context(section))
    result = decode(token, **context(section))
    assert result == make(section) and isinstance(result, TYPES[section])
    assert unpack(token)['at'] == '2026-10-14T12:34:56.123456Z'
    assert encode(result, **context(section)) == token
    assert len(token) < MAX_CLIENT_CARD_CURSOR_LENGTH and '=' not in token


@pytest.mark.parametrize('section', TYPES)
def test_none_is_first_page_and_no_next_page(section):
    assert decode(None, **context(section)) is None
    assert encode(None, **context(section)) is None


@pytest.mark.parametrize('section', TYPES)
@pytest.mark.parametrize('field', ['tenant_id', 'user_id', 'client_id', 'section'])
def test_cursor_cannot_be_reused_in_another_context(section, field):
    token = encode(make(section), **context(section))
    options = context(section)
    options[field] = ('activities' if section != 'activities' else 'history') if field == 'section' else uuid4()
    with pytest.raises(ConstructionClientValidationError): decode(token, **options)
    if field != 'section':
        with pytest.raises(ConstructionClientValidationError): encode(make(section), **options)


@pytest.mark.parametrize('token', ['', ' ', '=', '@@@', 'a', 'é', 'a/b+c', 'x'*(MAX_CLIENT_CARD_CURSOR_LENGTH+1),
    123, True, {}, [], b'abc', 'abc\n', 'abc=', 'null'])
def test_malformed_encoding_is_generic_validation_error(token):
    with pytest.raises(ConstructionClientValidationError, match=r'^Client card cursor is invalid\.$'):
        decode(token, **context('projects'))


@pytest.mark.parametrize('fault', ['extra', 'missing', 'version', 'bool_version', 'section_type', 'uuid_type',
    'uuid_compact', 'uuid_upper', 'time_type', 'naive', 'offset', 'invalid_time', 'noncanonical_time', 'bad_id'])
def test_payload_schema_and_canonical_values_are_required(fault):
    record = unpack(encode(make('history'), **context('history')))
    if fault == 'extra': record['secret'] = 'never-echo-this'
    if fault == 'missing': del record['id']
    if fault == 'version': record['v'] = 2
    if fault == 'bool_version': record['v'] = True
    if fault == 'section_type': record['section'] = []
    if fault == 'uuid_type': record['tenant_id'] = 123
    if fault == 'uuid_compact': record['tenant_id'] = TENANT.hex
    if fault == 'uuid_upper': record['tenant_id'] = str(TENANT).upper()
    if fault == 'time_type': record['at'] = {}
    if fault == 'naive': record['at'] = '2026-10-14T12:34:56.123456'
    if fault == 'offset': record['at'] = '2026-10-14T15:34:56.123456+03:00'
    if fault == 'invalid_time': record['at'] = 'private-invalid-date'
    if fault == 'noncanonical_time': record['at'] = '2026-10-14T12:34:56Z'
    if fault == 'bad_id': record['id'] = 'private-invalid-uuid'
    with pytest.raises(ConstructionClientValidationError, match=r'^Client card cursor is invalid\.$'):
        decode(raw_token(record), **context('history'))


@pytest.mark.parametrize('raw', [b'null', b'[]', b'{}', b'1', b'"abc"', b'{', b'\xff', b'{"v":1,"v":1}',
    b'['*400+b'0'+b']'*400])
def test_json_is_bounded_flat_object_without_duplicate_keys(raw):
    with pytest.raises(ConstructionClientValidationError): decode(raw_token(raw), **context('projects'))


def test_noncanonical_json_and_alternate_base64_bits_are_rejected():
    token = encode(make('projects'), **context('projects'))
    record = unpack(token)
    with pytest.raises(ConstructionClientValidationError):
        decode(raw_token(json.dumps(record, indent=1).encode('ascii')), **context('projects'))
    assert decode(token, **context('projects')) == make('projects')
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    # A one-byte raw value has four non-significant low bits in its last base64
    # character. Reject an alternate token even before JSON validation.
    canonical = raw_token(b'1')
    alternate = canonical[:-1]+alphabet[alphabet.index(canonical[-1])+1]
    with pytest.raises(ConstructionClientValidationError): decode(alternate, **context('projects'))


@pytest.mark.parametrize('section', TYPES)
def test_wrong_cursor_class_and_tampered_position_are_rejected(section):
    other = next(name for name in TYPES if name != section)
    with pytest.raises(ConstructionClientValidationError): encode(make(other), **context(section))
    cursor = make(section)
    field = 'due_at' if section == 'activities' else 'created_at' if section == 'history' else 'updated_at'
    object.__setattr__(cursor, field, datetime(2026, 10, 14))
    with pytest.raises(ConstructionClientValidationError): encode(cursor, **context(section))


@pytest.mark.parametrize('field,value', [('section', 'unknown'), ('section', []), ('tenant_id', str(TENANT)),
    ('user_id', None), ('client_id', str(CLIENT))])
def test_trusted_context_requires_server_uuid_values(field, value):
    options = context('projects'); options[field] = value
    for call, cursor in ((decode, None), (encode, None)):
        with pytest.raises(ConstructionClientValidationError): call(cursor, **options)


def test_position_is_not_an_authorization_credential_or_signature():
    # Canonical positions can be edited by a caller. They never confer access;
    # real read services must apply their tenant/membership/scope checks again.
    record = unpack(encode(make('projects'), **context('projects')))
    record['id'] = str(UUID(int=77))
    decoded = decode(raw_token(record), **context('projects'))
    assert decoded.project_id == UUID(int=77)
    assert decoded.tenant_id == TENANT and decoded.user_id == USER and decoded.client_id == CLIENT
