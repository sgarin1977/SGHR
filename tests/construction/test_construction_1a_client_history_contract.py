from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from services.construction.construction_audit import (
    CONSTRUCTION_AUDIT_EVENT_TYPES, CONSTRUCTION_STAGE_1A_AUDIT_EVENT_TYPES,
    ConstructionAuditLogger, ConstructionAuditChange,
)
from services.construction.construction_client_history_contract import (
    CONSTRUCTION_CLIENT_HISTORY_EVENT_ENTITY as ENTITIES,
    CONSTRUCTION_CLIENT_HISTORY_EVENT_OPERATION as OPERATIONS,
    construction_client_history_event_allowed, resolve_construction_history_event_client as resolve,
    construction_history_event_belongs_to_client as belongs,
)


TENANT, CLIENT, OTHER = uuid4(), uuid4(), uuid4()


def conversion_changes(client=CLIENT):
    return [dict(field='status', old_value='contacted', new_value='won'),
        dict(field='converted_client_id', old_value=None, new_value=str(client)),
        dict(field='converted_project_id', old_value=None, new_value=str(uuid4()))]


def event(kind='construction_project_created', marker=True):
    payload = dict(operation=OPERATIONS[kind], changes=[], reason=None)
    if kind == 'construction_lead_converted':
        payload['changes'] = conversion_changes()
        payload.update(created_client_id=str(CLIENT), created_project_id=None)
    if marker: payload['related_client_id'] = str(CLIENT)
    return SimpleNamespace(id=uuid4(), tenant_id=TENANT, event_type=kind, entity_type=ENTITIES[kind],
        entity_id=CLIENT if ENTITIES[kind] == 'construction_client' else uuid4(), payload=payload)


def test_exact_domain_pairs_are_read_only_and_subset_of_existing_write_registry():
    assert len(ENTITIES) == len(OPERATIONS) == 12
    assert set(ENTITIES) <= CONSTRUCTION_AUDIT_EVENT_TYPES | CONSTRUCTION_STAGE_1A_AUDIT_EVENT_TYPES
    assert set(ENTITIES.values()) == {'construction_client', 'construction_lead', 'construction_project', 'construction_activity'}
    with pytest.raises(TypeError): ENTITIES['admin_event'] = 'construction_client'
    with pytest.raises(TypeError): OPERATIONS['admin_event'] = 'create'


@pytest.mark.parametrize('kind', tuple(ENTITIES))
def test_all_registered_domain_events_resolve_canonical_operation_time_marker(kind):
    item = event(kind)
    original = deepcopy(vars(item))
    assert construction_client_history_event_allowed(item, tenant_id=TENANT)
    assert resolve(item, tenant_id=TENANT) == CLIENT
    assert belongs(item, tenant_id=TENANT, client_id=CLIENT)
    assert not belongs(item, tenant_id=TENANT, client_id=OTHER)
    assert vars(item) == original


@pytest.mark.parametrize('kind', tuple(ENTITIES))
def test_wrong_event_entity_pair_cannot_enter_card_history(kind):
    item = event(kind)
    item.entity_type = 'construction_lead' if ENTITIES[kind] != 'construction_lead' else 'construction_client'
    assert not construction_client_history_event_allowed(item, tenant_id=TENANT)
    assert resolve(item, tenant_id=TENANT) is None


@pytest.mark.parametrize('kind', ['admin_action', 'root_security_event', 'hr_employee_changed',
    'financial_payment_created', 'security_login', 'platform_company_profile_created',
    'construction_invitation_created', 'construction_access_grant_created', 'construction_project_area_created',
    'construction_client_created_extra', 'construction_unknown'])
def test_other_domains_and_prefix_matches_are_never_permitted(kind):
    item = event('construction_client_created')
    item.event_type = kind
    assert not construction_client_history_event_allowed(item, tenant_id=TENANT)
    assert resolve(item, tenant_id=TENANT) is None


@pytest.mark.parametrize('field,value', [('tenant_id', OTHER), ('id', None), ('id', 'not-a-uuid'),
    ('entity_id', None), ('entity_id', 'not-a-uuid'), ('event_type', []), ('entity_type', None)])
def test_malformed_or_foreign_event_binding_is_denied(field, value):
    item = event()
    setattr(item, field, value)
    assert resolve(item, tenant_id=TENANT) is None


@pytest.mark.parametrize('marker', [None, '', ' ', 'not-a-uuid', CLIENT.hex, str(CLIENT).upper(), 123, True, {}, []])
def test_present_invalid_marker_never_falls_back_to_client_entity(marker):
    item = event('construction_client_updated')
    item.payload['related_client_id'] = marker
    assert resolve(item, tenant_id=TENANT) is None


def test_intrinsic_client_identity_cannot_be_overridden_by_conflicting_marker():
    item = event('construction_client_created')
    item.payload['related_client_id'] = str(OTHER)
    assert resolve(item, tenant_id=TENANT) is None


@pytest.mark.parametrize('kind', ['construction_client_created', 'construction_client_updated', 'construction_client_archived'])
def test_markerless_client_events_have_intrinsic_identity_without_entity_lookup(kind):
    item = event(kind, marker=False)
    assert resolve(item, tenant_id=TENANT) == CLIENT
    item.archived_entity = True
    assert resolve(item, tenant_id=TENANT) == CLIENT


def test_legacy_project_client_change_uses_after_value_and_is_stable_after_future_reassignment():
    item = event('construction_project_updated', marker=False)
    item.payload['changes'] = [dict(field='client_id', old_value=str(OTHER), new_value=str(CLIENT))]
    assert resolve(item, tenant_id=TENANT) == CLIENT
    assert not belongs(item, tenant_id=TENANT, client_id=OTHER)
    item.current_project = SimpleNamespace(client_id=OTHER, deleted_at='archived')
    assert resolve(item, tenant_id=TENANT) == CLIENT


def test_marker_and_explicit_project_change_must_not_conflict():
    item = event('construction_project_updated')
    item.payload['changes'] = [dict(field='client_id', old_value=None, new_value=str(OTHER))]
    assert resolve(item, tenant_id=TENANT) is None


@pytest.mark.parametrize('new_value', [None, 'invalid', CLIENT.hex])
def test_legacy_project_unlink_or_invalid_change_does_not_attribute_to_old_client(new_value):
    item = event('construction_project_updated', marker=False)
    item.payload['changes'] = [dict(field='client_id', old_value=str(CLIENT), new_value=new_value)]
    assert resolve(item, tenant_id=TENANT) is None


def test_legacy_conversion_changes_prove_client_at_operation_time():
    item = event('construction_lead_converted', marker=False)
    assert resolve(item, tenant_id=TENANT) == CLIENT
    item.current_lead = SimpleNamespace(converted_client_id=OTHER, deleted_at='archived')
    assert resolve(item, tenant_id=TENANT) == CLIENT


@pytest.mark.parametrize('fault', ['no_client_change', 'no_status_change', 'not_won', 'old_terminal',
    'duplicate_client_change', 'conflicting_created_client', 'conflicting_marker', 'malformed_client_change'])
def test_incomplete_or_conflicting_conversion_proof_is_not_used(fault):
    item = event('construction_lead_converted', marker=False)
    if fault == 'no_client_change': item.payload['changes'] = item.payload['changes'][:1]
    if fault == 'no_status_change': item.payload['changes'] = item.payload['changes'][1:]
    if fault == 'not_won': item.payload['changes'][0]['new_value'] = 'lost'
    if fault == 'old_terminal': item.payload['changes'][0]['old_value'] = 'won'
    if fault == 'duplicate_client_change': item.payload['changes'].append(deepcopy(item.payload['changes'][1]))
    if fault == 'conflicting_created_client': item.payload['created_client_id'] = str(OTHER)
    if fault == 'conflicting_marker': item.payload['related_client_id'] = str(OTHER)
    if fault == 'malformed_client_change': item.payload['changes'][1]['new_value'] = 'invalid'
    assert resolve(item, tenant_id=TENANT) is None


@pytest.mark.parametrize('kind', ['construction_project_created', 'construction_project_updated',
    'construction_project_status_changed', 'construction_lead_created', 'construction_lead_status_changed',
    'construction_activity_created', 'construction_activity_completed', 'construction_activity_cancelled'])
def test_legacy_event_is_not_guessed_from_current_mutable_relationships_or_contacts(kind):
    item = event(kind, marker=False)
    item.current_entity = SimpleNamespace(client_id=CLIENT, converted_client_id=CLIENT, email='matching@example.com')
    item.payload.update(email='matching@example.com', client_id=str(CLIENT), current_client_id=str(CLIENT))
    assert resolve(item, tenant_id=TENANT) is None


@pytest.mark.parametrize('fault', ['not_dict', 'wrong_operation', 'not_list', 'bad_change', 'blank_field', 'duplicate_field'])
def test_invalid_payload_shape_cannot_become_a_history_proof(fault):
    item = event()
    if fault == 'not_dict': item.payload = None
    if fault == 'wrong_operation': item.payload['operation'] = 'admin'
    if fault == 'not_list': item.payload['changes'] = {}
    if fault == 'bad_change': item.payload['changes'] = [dict(field='name', new_value='x')]
    if fault == 'blank_field': item.payload['changes'] = [dict(field='', old_value=None, new_value='x')]
    if fault == 'duplicate_field': item.payload['changes'] = [dict(field='name', old_value=None, new_value='x')]*2
    assert resolve(item, tenant_id=TENANT) is None


def test_client_marker_attribution_does_not_follow_future_entity_state():
    item = event()
    before = deepcopy(vars(item))
    for changed_client in (None, OTHER):
        item.current_entity = SimpleNamespace(client_id=changed_client, deleted_at='archived')
        assert resolve(item, tenant_id=TENANT) == CLIENT
    assert item.payload == before['payload']


@pytest.mark.parametrize('context', [None, 'not-a-uuid', True])
def test_context_must_be_canonical_uuid(context):
    with pytest.raises(ValueError): resolve(event(), tenant_id=context)
    with pytest.raises(ValueError): belongs(event(), tenant_id=TENANT, client_id=context)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', tuple(ENTITIES))
async def test_current_audit_logger_events_are_compatible_with_history_contract(kind):
    sample = event(kind)
    actor = SimpleNamespace(tenant_id=TENANT, user_id=uuid4())
    repo = SimpleNamespace(create_event=AsyncMock())
    logger = ConstructionAuditLogger(event_repository=repo)
    details = {key: value for key, value in sample.payload.items() if key not in ('operation', 'changes', 'reason')}
    changes = tuple(ConstructionAuditChange(**dict(field=change['field'], old_value=change['old_value'],
        new_value=change['new_value'])) for change in sample.payload['changes'])
    await logger.record_operation(actor=actor, event_type=kind, entity_type=sample.entity_type,
        entity_id=sample.entity_id, operation=sample.payload['operation'], details=details, changes=changes)
    saved = SimpleNamespace(id=uuid4(), **repo.create_event.await_args.kwargs)
    assert resolve(saved, tenant_id=TENANT) == CLIENT
    repo.create_event.assert_awaited_once()
