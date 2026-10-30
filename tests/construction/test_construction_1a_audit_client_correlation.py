import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from services.construction.construction_audit import ConstructionAuditContractError, ConstructionAuditLogger
from services.construction.construction_audit_client_correlation import construction_audit_client_details


TENANT = UUID("f4c9ab96-1a72-48a0-8eb1-12e55cbd7c33")
CLIENT_ID = UUID("74b343bf-1848-4934-8b10-e369ad488ecc")


def row(**fields):
    return SimpleNamespace(id=uuid4(), tenant_id=TENANT, **fields)


def correlate(kind, entity, **evidence):
    return construction_audit_client_details(
        tenant_id=TENANT, entity_type="construction_" + kind,
        entity=entity, **evidence,
    )


@pytest.mark.parametrize("kind,fields", [
    ("client", {}), ("project", {"client_id": CLIENT_ID}),
    ("lead", {"converted_client_id": CLIENT_ID}),
])
def test_direct_correlation_uses_canonical_entity_relationship(kind, fields):
    entity = row(**fields)
    expected = entity.id if kind == "client" else CLIENT_ID
    assert correlate(kind, entity) == {"related_client_id": str(expected)}


@pytest.mark.parametrize("kind,field", [("project", "client_id"), ("lead", "converted_client_id")])
def test_unlinked_entity_has_no_marker_even_if_contacts_match(kind, field):
    entity = row(**{field: None}, email="same@example.com", notes=str(CLIENT_ID))
    assert correlate(kind, entity) == {}


@pytest.mark.parametrize("mask", range(1, 8))
def test_activity_all_link_combinations_use_one_client_snapshot(mask):
    client = row(); client.id = CLIENT_ID
    lead = row(converted_client_id=CLIENT_ID)
    project = row(client_id=CLIENT_ID)
    evidence = dict(client=client if mask & 1 else None,
                    lead=lead if mask & 2 else None, project=project if mask & 4 else None)
    entity = row(client_id=client.id if mask & 1 else None,
                 lead_id=lead.id if mask & 2 else None, project_id=project.id if mask & 4 else None)
    assert correlate("activity", entity, **evidence) == {"related_client_id": str(CLIENT_ID)}


def test_activity_before_conversion_has_no_marker_and_after_conversion_has_one():
    lead = row(converted_client_id=None)
    entity = row(client_id=None, lead_id=lead.id, project_id=None)
    before = correlate("activity", entity, lead=lead)
    lead.converted_client_id = CLIENT_ID
    after = correlate("activity", entity, lead=lead)
    assert before == {}
    assert after == {"related_client_id": str(CLIENT_ID)}


def test_activity_known_and_unknown_links_do_not_guess_missing_clients():
    lead = row(converted_client_id=None)
    project = row(client_id=CLIENT_ID)
    entity = row(client_id=None, lead_id=lead.id, project_id=project.id)
    assert correlate("activity", entity, lead=lead, project=project) == {"related_client_id": str(CLIENT_ID)}
    project.client_id = None
    assert correlate("activity", entity, lead=lead, project=project) == {}


@pytest.mark.parametrize("pair", ["client_lead", "client_project", "lead_project"])
def test_conflicting_client_links_never_select_an_arbitrary_client(pair):
    client = row(); client.id = CLIENT_ID
    lead = row(converted_client_id=uuid4())
    project = row(client_id=uuid4())
    entity = row(client_id=client.id if "client" in pair else None,
                 lead_id=lead.id if "lead" in pair else None,
                 project_id=project.id if "project" in pair else None)
    evidence = {name: value for name, value in dict(client=client, lead=lead, project=project).items()
                if name in pair}
    assert correlate("activity", entity, **evidence) == {}


@pytest.mark.parametrize("kind,fields", [
    ("client", {}), ("lead", {"converted_client_id": CLIENT_ID}),
    ("project", {"client_id": CLIENT_ID}),
])
def test_archived_entities_keep_canonical_correlation(kind, fields):
    entity = row(deleted_at="archived", **fields)
    assert correlate(kind, entity)


def test_archived_related_project_is_evidence_not_an_active_list_filter():
    project = row(client_id=CLIENT_ID, deleted_at="archived")
    entity = row(client_id=None, lead_id=None, project_id=project.id, deleted_at="archived")
    assert correlate("activity", entity, project=project) == {"related_client_id": str(CLIENT_ID)}


@pytest.mark.parametrize("field,value", [
    ("id", None), ("id", str(CLIENT_ID)), ("tenant_id", uuid4()), ("tenant_id", str(TENANT)),
])
def test_invalid_entity_or_foreign_tenant_is_rejected(field, value):
    entity = row(client_id=CLIENT_ID); setattr(entity, field, value)
    with pytest.raises(ConstructionAuditContractError): correlate("project", entity)


@pytest.mark.parametrize("value", ["", str(CLIENT_ID), False, 1, {}, []])
def test_reference_must_be_a_canonical_uuid_or_null(value):
    with pytest.raises(ConstructionAuditContractError): correlate("project", row(client_id=value))


def test_missing_reference_is_not_treated_as_no_client():
    with pytest.raises(ConstructionAuditContractError): correlate("project", row())


@pytest.mark.parametrize("field", ["client", "lead", "project"])
@pytest.mark.parametrize("fault", ["missing", "foreign", "wrong_id", "string_id"])
def test_activity_evidence_must_match_every_link(field, fault):
    related = row(converted_client_id=CLIENT_ID, client_id=CLIENT_ID)
    links = dict(client_id=None, lead_id=None, project_id=None); links[field + "_id"] = related.id
    entity = row(**links)
    if fault == "missing": related = None
    elif fault == "foreign": related.tenant_id = uuid4()
    elif fault == "wrong_id": related.id = uuid4()
    else: related.id = str(related.id)
    with pytest.raises(ConstructionAuditContractError): correlate("activity", entity, **{field: related})


@pytest.mark.parametrize("kind", ["client", "lead", "project", "activity"])
def test_unused_evidence_is_rejected(kind):
    entity = row(client_id=None, lead_id=None, project_id=None, converted_client_id=None)
    with pytest.raises(ConstructionAuditContractError): correlate(kind, entity, project=row(client_id=CLIENT_ID))


def test_activity_without_links_is_not_valid_correlation_evidence():
    with pytest.raises(ConstructionAuditContractError):
        correlate("activity", row(client_id=None, lead_id=None, project_id=None))


@pytest.mark.parametrize("kind", ["construction_invitation", "construction_access_grant", "admin", ""])
def test_unrelated_domains_cannot_use_client_correlation(kind):
    with pytest.raises(ConstructionAuditContractError):
        construction_audit_client_details(tenant_id=TENANT, entity_type=kind, entity=row())


@pytest.mark.parametrize("tenant", [None, str(TENANT), 1])
def test_untrusted_tenant_identifier_is_rejected(tenant):
    with pytest.raises(ConstructionAuditContractError):
        construction_audit_client_details(tenant_id=tenant, entity_type="construction_client", entity=row())


def test_existing_audit_logger_persists_operation_time_marker_without_retaining_rows():
    class Events:
        def __init__(self): self.calls = []
        async def create_event(self, **values): self.calls.append(values); return values
    events = Events()
    entity = row(client_id=CLIENT_ID)
    details = correlate("project", entity)
    asyncio.run(ConstructionAuditLogger(event_repository=events).record_operation(
        actor=SimpleNamespace(tenant_id=TENANT, user_id=uuid4()),
        event_type="construction_project_updated", entity_type="construction_project",
        entity_id=entity.id, operation="update", details=details,
    ))
    entity.client_id = uuid4()
    details["related_client_id"] = str(entity.client_id)
    assert events.calls[0]["payload"]["related_client_id"] == str(CLIENT_ID)
    assert events.calls[0]["tenant_id"] == TENANT
    assert events.calls[0]["payload"]["operation"] == "update"
    assert events.calls[0]["payload"]["changes"] == []
    assert len(events.calls) == 1


def test_preparation_does_not_repair_old_events_or_mutate_resources():
    entity = row(client_id=CLIENT_ID)
    old_payload = {"operation": "update", "changes": []}
    before = dict(vars(entity))
    result = correlate("project", entity)
    result["related_client_id"] = str(uuid4())
    assert vars(entity) == before
    assert old_payload == {"operation": "update", "changes": []}
    assert correlate("project", entity) == {"related_client_id": str(CLIENT_ID)}
