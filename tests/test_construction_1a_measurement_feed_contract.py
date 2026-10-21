"""CON-C016 schema boundary: local validation and current app inspection only."""
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.construction_measurement_feed_contract import (
    MEASUREMENT_FEED_PATH, MeasurementFeedEnvelope, MeasurementFeedItem,
    measurement_feed_openapi_contract,
)
from api.responses import success_envelope


def item(**changes):
    return dict(id=uuid4(), project_id=uuid4(), number='0049', project_name='Casa João',
        status='draft', created_at=datetime(2026, 10, 21, 12, tzinfo=timezone.utc), **changes)


@pytest.mark.parametrize('number', ['0001', '0049', '9999', '10000', '123456789'])
def test_already_assigned_display_numbers_keep_four_digits_and_can_grow(number):
    values = item()
    values['number'] = number
    model = MeasurementFeedItem.model_validate(values)
    assert model.number == number
    assert json.loads(model.model_dump_json())['number'] == number


@pytest.mark.parametrize('number', ['0000', '49', '00049', '-001', ' 0049', '0049 ', 49, True])
def test_feed_rejects_unassigned_or_noncanonical_display_numbers(number):
    values = item()
    values['number'] = number
    with pytest.raises(ValidationError):
        MeasurementFeedItem.model_validate(values)


@pytest.mark.parametrize('field,value', [
    ('created_at', datetime(2026, 10, 21)),
    ('project_name', '   '),
    ('project_id', 'not-an-id'),
    ('status', 'В работе'),
    ('status', ''),
])
def test_projection_rejects_missing_context_or_invalid_structural_values(field, value):
    values = item()
    values[field] = value
    with pytest.raises(ValidationError):
        MeasurementFeedItem.model_validate(values)


def test_feed_contract_does_not_guess_a_closed_lifecycle_from_crm():
    values = item()
    values['status'] = 'future_measurement_state'
    model = MeasurementFeedItem.model_validate(values)
    assert model.status == values['status']
    status_schema = MeasurementFeedItem.model_json_schema()['properties']['status']
    assert 'enum' not in status_schema
    assert 'Canonical Measurement lifecycle codes' in measurement_feed_openapi_contract()['x-sghr-deferred-details']


@pytest.mark.parametrize('extra', ['tenant_id', 'linked_user_id', 'cost', 'margin', 'token'])
def test_public_feed_does_not_accept_unrelated_or_secret_fields(extra):
    values = item()
    values[extra] = 'unrelated'
    with pytest.raises(ValidationError):
        MeasurementFeedItem.model_validate(values)


def test_common_platform_envelope_is_preserved_and_json_roundtrips():
    row = MeasurementFeedItem.model_validate(item())
    data = success_envelope(data=[row.model_dump(mode='json')], request_id='feed-contract')
    model = MeasurementFeedEnvelope.model_validate(data)
    assert len(model.data) == 1 and model.meta == {} and model.request_id == 'feed-contract'
    assert MeasurementFeedEnvelope.model_validate_json(model.model_dump_json()) == model


def test_openapi_document_has_resolvable_local_refs_and_existing_platform_security():
    document = measurement_feed_openapi_contract()
    json.dumps(document)
    operation = document['paths'][MEASUREMENT_FEED_PATH]['get']
    assert operation['security'] == [{'BearerAuth': []}]
    assert operation['x-sghr-permission'] == 'construction.measurements.read'
    assert operation['x-sghr-modules'] == ['construction', 'construction.core']
    assert operation['parameters'][0]['schema'] == {'type': 'boolean', 'const': True}
    def walk(value):
        if isinstance(value, dict):
            if '$ref' in value:
                current = document
                for key in value['$ref'].removeprefix('#/').split('/'):
                    current = current[key]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(document)


def test_contract_export_is_isolated_and_cannot_mutate_later_exports():
    first = measurement_feed_openapi_contract()
    first['components']['schemas'].clear()
    second = measurement_feed_openapi_contract()
    assert second['components']['schemas']['MeasurementFeedEnvelope']['required'] == ['data', 'meta', 'request_id']
    assert second['x-sghr-contract-only'] is True
    assert second['x-sghr-runtime-registered'] is False
    assert second['x-sghr-implementation-stage'] == 'Measurement'


def test_contract_has_no_runtime_route_or_surrogate_measurement_table():
    from api.app import create_app
    from database.models import Base
    before = set(Base.metadata.tables)
    document = measurement_feed_openapi_contract()
    app = create_app()
    assert MEASUREMENT_FEED_PATH not in app.openapi()['paths']
    assert not any(getattr(route, 'path', None) == MEASUREMENT_FEED_PATH for route in app.routes)
    assert set(Base.metadata.tables) == before
    assert not any('measurement' in name for name in before)
    assert set(document['paths']) == {MEASUREMENT_FEED_PATH}
