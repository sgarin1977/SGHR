"""Pure event-time client history evidence; no queries, authorization or repair."""

from dataclasses import dataclass

from datetime import datetime, timezone

from types import MappingProxyType
from uuid import UUID

from services.construction.construction_lead_rules import CONSTRUCTION_LEAD_STATUSES, CONSTRUCTION_LEAD_TERMINAL_STATUSES


CONSTRUCTION_CLIENT_HISTORY_EVENT_ENTITY = MappingProxyType({
    'construction_client_created': 'construction_client',
    'construction_client_updated': 'construction_client',
    'construction_client_archived': 'construction_client',
    'construction_project_created': 'construction_project',
    'construction_project_updated': 'construction_project',
    'construction_project_status_changed': 'construction_project',
    'construction_lead_created': 'construction_lead',
    'construction_lead_status_changed': 'construction_lead',
    'construction_lead_converted': 'construction_lead',
    'construction_activity_created': 'construction_activity',
    'construction_activity_completed': 'construction_activity',
    'construction_activity_cancelled': 'construction_activity',
})

CONSTRUCTION_CLIENT_HISTORY_EVENT_OPERATION = MappingProxyType({
    'construction_client_created': 'create',
    'construction_client_updated': 'update',
    'construction_client_archived': 'archive',
    'construction_project_created': 'create',
    'construction_project_updated': 'update',
    'construction_project_status_changed': 'status_change',
    'construction_lead_created': 'create',
    'construction_lead_status_changed': 'transition_status',
    'construction_lead_converted': 'convert',
    'construction_activity_created': 'create',
    'construction_activity_completed': 'complete',
    'construction_activity_cancelled': 'cancel',
})


_MISSING = object()
_INVALID = object()


@dataclass(frozen=True)
class ConstructionHistoryClientOnlyActivityEvidence:
    """Repository proof from an immutable, direct-client-only activity link.

    The guarded repository join excludes lead_id/project_id. This object never
    comes from an HTTP request and is not an audit payload or public response.
    """
    activity_id: UUID
    tenant_id: UUID
    client_id: UUID
    created_at: datetime

def _utc_instant(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    if value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)

def _legacy_client_only_activity(event, *, tenant_id):
    proof = getattr(event, 'activity_evidence', None)
    if (event.entity_type != 'construction_activity'
        or not isinstance(proof, ConstructionHistoryClientOnlyActivityEvidence)
        or proof.activity_id != event.entity_id or proof.tenant_id != tenant_id
        or not all(isinstance(value, UUID) for value in (proof.activity_id, proof.tenant_id, proof.client_id))):
        return None
    created, happened = _utc_instant(proof.created_at), _utc_instant(getattr(event, 'created_at', None))
    if created is None or happened is None or happened < created:
        return None
    # Existing activity operations never edit link fields. An event claiming
    # such a mutation cannot use this immutable-link fallback.
    if any(change['field'] in ('client_id', 'lead_id', 'project_id') for change in event.payload['changes']):
        return None
    return proof.client_id

def _canonical_uuid(value):
    if not isinstance(value, str):
        return None
    try:
        checked = UUID(value)
    except (ValueError, AttributeError):
        return None
    return checked if str(checked) == value else None


def _valid_changes(payload):
    changes = payload.get('changes')
    if not isinstance(changes, list):
        return False
    seen = set()
    for item in changes:
        if (not isinstance(item, dict) or set(item) != {'field', 'old_value', 'new_value'}
            or not isinstance(item['field'], str) or not item['field'].strip() or item['field'] in seen):
            return False
        seen.add(item['field'])
    return True


def _change(payload, field):
    if not _valid_changes(payload):
        return _INVALID
    return next((item for item in payload['changes'] if item['field'] == field), _MISSING)


def _client_change(payload, field):
    change = _change(payload, field)
    if change is _MISSING or change is _INVALID:
        return change
    if change['old_value'] is not None and _canonical_uuid(change['old_value']) is None:
        return _INVALID
    value = change['new_value']
    if value is None:
        return None
    return _canonical_uuid(value) or _INVALID


def construction_client_history_event_allowed(event, *, tenant_id):
    """Exact entity/event allowlist in a trusted tenant, independent of membership."""
    if not isinstance(tenant_id, UUID):
        raise ValueError('Client history requires a trusted tenant UUID.')
    kind, entity = getattr(event, 'event_type', None), getattr(event, 'entity_type', None)
    return (isinstance(kind, str) and kind in CONSTRUCTION_CLIENT_HISTORY_EVENT_ENTITY
        and entity == CONSTRUCTION_CLIENT_HISTORY_EVENT_ENTITY[kind]
        and getattr(event, 'tenant_id', None) == tenant_id
        and isinstance(getattr(event, 'id', None), UUID)
        and isinstance(getattr(event, 'entity_id', None), UUID))


def resolve_construction_history_event_client(event, *, tenant_id):
    """Return a UUID only when this event proves operation-time attribution.

    A present marker is authoritative, with invalid/conflicting evidence failing
    closed. It is never replaced using today's mutable entity relationships.
    Marker-less fallback here uses event data only: intrinsic client identity,
    explicit project reassignment and an explicit completed lead conversion.
    A guarded client-only activity snapshot can prove an immutable legacy link.
    Current mutable project/lead links never reconstruct a past client.
    """
    if not construction_client_history_event_allowed(event, tenant_id=tenant_id):
        return None
    payload = getattr(event, 'payload', None)
    if not isinstance(payload, dict):
        return None
    kind = event.event_type
    if payload.get('operation') != CONSTRUCTION_CLIENT_HISTORY_EVENT_OPERATION[kind]:
        return None
    # Validate the audit changes shape even when it is not used for attribution.
    if not _valid_changes(payload):
        return None
    intrinsic = event.entity_id if event.entity_type == 'construction_client' else _MISSING
    explicit = _MISSING
    if kind == 'construction_project_updated':
        explicit = _client_change(payload, 'client_id')
    elif kind == 'construction_lead_converted':
        explicit = _client_change(payload, 'converted_client_id')
        status = _change(payload, 'status')
        if (explicit is _MISSING or explicit is _INVALID or explicit is None
            or status is _MISSING or status is _INVALID
            or status['new_value'] != 'won'
            or not isinstance(status['old_value'], str)
            or status['old_value'] not in CONSTRUCTION_LEAD_STATUSES - CONSTRUCTION_LEAD_TERMINAL_STATUSES):
            return None
        created = payload.get('created_client_id')
        if created is not None and _canonical_uuid(created) != explicit:
            return None
    if explicit is _INVALID:
        return None
    if 'related_client_id' in payload:
        marker = _canonical_uuid(payload['related_client_id'])
        if marker is None:
            return None
        if intrinsic is not _MISSING and marker != intrinsic:
            return None
        if explicit is not _MISSING and marker != explicit:
            return None
        return marker
    if intrinsic is not _MISSING:
        return intrinsic
    if isinstance(explicit, UUID):
        return explicit
    return _legacy_client_only_activity(event, tenant_id=tenant_id)


def construction_history_event_belongs_to_client(event, *, tenant_id, client_id):
    if not isinstance(client_id, UUID):
        raise ValueError('Client history requires a client UUID.')
    return resolve_construction_history_event_client(event, tenant_id=tenant_id) == client_id
