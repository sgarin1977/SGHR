from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, or_, not_, select, func, case, type_coerce, literal, extract, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import Grouping

from database.models import ConstructionActivity, ConstructionClient, EventLog
from services.construction.construction_client_history_contract import (
    CONSTRUCTION_CLIENT_HISTORY_EVENT_ENTITY, CONSTRUCTION_CLIENT_HISTORY_EVENT_OPERATION,
    construction_history_event_belongs_to_client, ConstructionHistoryClientOnlyActivityEvidence,
)
from services.construction.construction_lead_rules import CONSTRUCTION_LEAD_STATUSES, CONSTRUCTION_LEAD_TERMINAL_STATUSES


_UUID_PATTERN = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

CLIENT_HISTORY_RECORD_FIELDS = ('id', 'tenant_id', 'user_id', 'event_type', 'entity_type', 'entity_id', 'payload', 'created_at')


CLIENT_HISTORY_ACTIVITY_EVIDENCE_FIELDS = ('history_activity_id', 'history_activity_tenant_id',
    'history_activity_client_id', 'history_activity_created_at')

@dataclass(frozen=True)
class ConstructionClientHistoryRecord:
    id: UUID
    tenant_id: UUID
    user_id: UUID | None
    event_type: str
    entity_type: str
    entity_id: UUID
    payload: dict
    created_at: datetime
    activity_evidence: ConstructionHistoryClientOnlyActivityEvidence | None = None


@dataclass(frozen=True)
class ConstructionClientHistoryCursor:
    tenant_id: UUID
    client_id: UUID
    created_at: datetime
    event_id: UUID

    def __post_init__(self):
        if any(not isinstance(value, UUID) for value in (self.tenant_id, self.client_id, self.event_id)):
            raise ValueError('Client history cursor requires tenant/client/event UUIDs.')
        if not isinstance(self.created_at, datetime):
            raise ValueError('Client history cursor requires an event timestamp.')
        # The existing EventLog ORM uses utcnow_naive; timezone-aware database
        # results are normalized to the same UTC timeline without schema changes.
        value = self.created_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        elif value.utcoffset() is None:
            raise ValueError('Client history cursor timezone is invalid.')
        object.__setattr__(self, 'created_at', value.astimezone(timezone.utc))

    @property
    def epoch(self):
        delta = self.created_at - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1000000)


def _json_object(expression):
    return type_coerce(Grouping(case((func.jsonb_typeof(expression) == 'object', expression),
        else_=literal({}, type_=JSONB))), JSONB)


def _json_uuid_or_null(expression):
    return or_(func.jsonb_typeof(expression) == 'null',
        and_(func.jsonb_typeof(expression) == 'string', expression.astext.op('~')(_UUID_PATTERN)))


def _changes(payload):
    raw = payload['changes']
    safe = case((func.jsonb_typeof(raw) == 'array', raw), else_=literal([], type_=JSONB))
    entries = func.jsonb_array_elements(safe).table_valued('value').alias('client_history_change')
    value = _json_object(type_coerce(entries.c.value, JSONB))
    required = and_(*(value.has_key(field) for field in ('field', 'old_value', 'new_value')))
    remainder = value.op('-')('field').op('-')('old_value').op('-')('new_value')
    bad = select(literal(1)).select_from(entries).where(or_(
        func.jsonb_typeof(entries.c.value) != 'object', not_(required),
        remainder != literal({}, type_=JSONB), func.jsonb_typeof(value['field']) != 'string',
        not_(value['field'].astext.op('~')('[^[:space:]]')))).correlate(EventLog).exists()
    duplicated = select(literal(1)).select_from(entries).group_by(value['field'].astext).having(
        func.count() > 1).correlate(EventLog).exists()
    valid = and_(func.jsonb_typeof(raw) == 'array', not_(bad), not_(duplicated))
    return entries, value, valid


def _client_history_predicate(*, tenant_id, client_id):
    model = EventLog
    payload = _json_object(model.payload)
    entries, change, valid_changes = _changes(payload)

    def exists_change(field, predicate=None):
        terms = [change['field'].astext == field]
        if predicate is not None:
            terms.append(predicate)
        return select(literal(1)).select_from(entries).where(*terms).correlate(model).exists()

    target = str(client_id)
    valid_client_change = and_(_json_uuid_or_null(change['old_value']),
        func.jsonb_typeof(change['new_value']) == 'string', change['new_value'].astext == target)
    project_change = exists_change('client_id', valid_client_change)
    project_has_change = exists_change('client_id')
    lead_change = exists_change('converted_client_id', valid_client_change)
    lead_status = exists_change('status', and_(change['old_value'].astext.in_(
        tuple(sorted(CONSTRUCTION_LEAD_STATUSES - CONSTRUCTION_LEAD_TERMINAL_STATUSES))),
        change['new_value'].astext == 'won'))
    created_client_valid = or_(not_(payload.has_key('created_client_id')),
        func.jsonb_typeof(payload['created_client_id']) == 'null',
        and_(func.jsonb_typeof(payload['created_client_id']) == 'string',
            payload['created_client_id'].astext == target))
    marker_present = payload.has_key('related_client_id')
    marker_target = and_(func.jsonb_typeof(payload['related_client_id']) == 'string',
        payload['related_client_id'].astext == target)
    marker_absent_or_target = or_(not_(marker_present), marker_target)
    intrinsic_client = and_(model.entity_type == 'construction_client', model.entity_id == client_id,
        marker_absent_or_target)
    lead_conversion = and_(model.event_type == 'construction_lead_converted', lead_change, lead_status,
        created_client_valid, marker_absent_or_target)
    project_update = and_(model.event_type == 'construction_project_updated',
        or_(and_(marker_target, not_(project_has_change)), and_(project_change, marker_absent_or_target)))
    remaining_marker = and_(model.entity_type != 'construction_client',
        model.event_type.not_in(('construction_lead_converted', 'construction_project_updated')), marker_target)
    activity = ConstructionActivity
    immutable_activity = and_(model.entity_type == 'construction_activity',
        not_(marker_present), activity.id == model.entity_id, activity.tenant_id == tenant_id,
        activity.client_id == client_id, activity.lead_id.is_(None), activity.project_id.is_(None),
        model.created_at >= activity.created_at,
        not_(exists_change('client_id')), not_(exists_change('lead_id')), not_(exists_change('project_id')))
    pairs = or_(*(and_(model.event_type == event_type, model.entity_type == entity_type,
        payload['operation'].astext == CONSTRUCTION_CLIENT_HISTORY_EVENT_OPERATION[event_type])
        for event_type, entity_type in CONSTRUCTION_CLIENT_HISTORY_EVENT_ENTITY.items()))
    return and_(model.tenant_id == tenant_id, model.entity_id.is_not(None),
        func.jsonb_typeof(model.payload) == 'object', pairs, valid_changes,
        or_(intrinsic_client, lead_conversion, project_update, remaining_marker, immutable_activity))


class ConstructionClientHistoryRepository:
    """Restricted event data access; caller owns authorization and read transaction."""

    def __init__(self, session):
        if session is None or not callable(getattr(session, 'execute', None)):
            raise ValueError('Client history repository requires a session.')
        self.session = session

    async def get_client_identity_for_history(self, *, tenant_id, client_id):
        """Read tenant identity including archived clients; no business data or lock."""
        if not isinstance(tenant_id, UUID) or not isinstance(client_id, UUID):
            raise ValueError('History client identity requires tenant/client UUIDs.')
        statement = select(ConstructionClient.id, ConstructionClient.tenant_id).where(
            ConstructionClient.tenant_id == tenant_id, ConstructionClient.id == client_id).limit(1).execution_options(autoflush=False)
        result = await self.session.execute(statement)
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def list_history_for_client(self, *, tenant_id, client_id, limit=20, after=None):
        if not isinstance(tenant_id, UUID) or not isinstance(client_id, UUID):
            raise ValueError('Client history requires tenant/client UUIDs.')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Client history page limit must be between 1 and 100.')
        if after is not None:
            if not isinstance(after, ConstructionClientHistoryCursor):
                raise ValueError('Client history cursor is invalid.')
            after = ConstructionClientHistoryCursor(after.tenant_id, after.client_id, after.created_at, after.event_id)
            if after.tenant_id != tenant_id or after.client_id != client_id:
                raise ValueError('Client history cursor belongs to another client or tenant.')
        filters = [_client_history_predicate(tenant_id=tenant_id, client_id=client_id)]
        if after is not None:
            # EPOCH supports the existing naive-UTC ORM and aware PostgreSQL
            # timestamps without a timezone-dependent datetime bind or UUID cast.
            position = literal(after.epoch, type_=Numeric())
            instant = extract('epoch', EventLog.created_at)
            filters.append(or_(instant < position, and_(instant == position, EventLog.id < after.event_id)))
        activity = ConstructionActivity
        evidence = (activity.id, activity.tenant_id, activity.client_id, activity.created_at)
        statement = select(*(getattr(EventLog, field) for field in CLIENT_HISTORY_RECORD_FIELDS),
            *(column.label(name) for column, name in zip(evidence, CLIENT_HISTORY_ACTIVITY_EVIDENCE_FIELDS))).select_from(
                EventLog).outerjoin(activity, and_(EventLog.entity_type == 'construction_activity',
                    activity.id == EventLog.entity_id, activity.tenant_id == EventLog.tenant_id,
                    activity.client_id.is_not(None), activity.lead_id.is_(None), activity.project_id.is_(None),
                    not_(_json_object(EventLog.payload).has_key('related_client_id'))
                )).where(*filters).order_by(EventLog.created_at.desc(), EventLog.id.desc()).limit(
            limit + 1).execution_options(populate_existing=True, autoflush=False)
        result = await self.session.execute(statement)
        rows = []
        for row in result.mappings().all():
            data = dict(row)
            values = tuple(data.pop(name, None) for name in CLIENT_HISTORY_ACTIVITY_EVIDENCE_FIELDS)
            proof = None
            if any(value is not None for value in values):
                if any(value is None for value in values):
                    raise ValueError('Client history activity evidence is incomplete.')
                proof = ConstructionHistoryClientOnlyActivityEvidence(*values)
            rows.append(ConstructionClientHistoryRecord(**data, activity_evidence=proof))
        if len(rows) > limit + 1:
            raise ValueError('Client history repository returned an oversized page.')
        previous = (after.created_at, after.event_id) if after is not None else None
        seen = set()
        for row in rows:
            if not construction_history_event_belongs_to_client(row, tenant_id=tenant_id, client_id=client_id):
                raise ValueError('Client history repository returned invalid evidence.')
            position = ConstructionClientHistoryCursor(tenant_id, client_id, row.created_at, row.id)
            key = (position.created_at, position.event_id)
            if row.id in seen or (previous is not None and key >= previous):
                raise ValueError('Client history repository returned invalid ordering.')
            previous = key
            seen.add(row.id)
        return rows
