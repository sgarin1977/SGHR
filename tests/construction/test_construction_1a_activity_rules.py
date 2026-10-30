from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from itertools import product
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from services.construction.construction_activity_rules import (
    CONSTRUCTION_ACTIVITY_TYPES, ConstructionActivityLinks, ConstructionActivitySchedule,
    ConstructionActivityTransitionError, ConstructionActivityValidationError,
    normalize_construction_activity_schedule, normalize_construction_activity_type,
    require_construction_activity_transition,
)

@pytest.mark.parametrize("value,expected", [
    ("call", "call"), (" MESSAGE ", "message"), ("meeting", "meeting"),
    ("site_visit", "site_visit"), ("follow_up", "follow_up"), ("task", "task"), ("other", "other"),
])
def test_activity_uses_only_confirmed_types(value, expected):
    assert normalize_construction_activity_type(value) == expected

@pytest.mark.parametrize("value", [None, True, 7, "", "unknown", "email", "planned"])
def test_activity_rejects_unapproved_types(value):
    with pytest.raises(ConstructionActivityValidationError):
        normalize_construction_activity_type(value)

@pytest.mark.parametrize("current,next_status", list(product(("planned", "completed", "cancelled"), repeat=2)))
def test_activity_transition_contract_and_terminal_states(current, next_status):
    if current == "planned" and next_status in ("completed", "cancelled"):
        assert require_construction_activity_transition(current_status=current, next_status=next_status) is None
    else:
        with pytest.raises(ConstructionActivityTransitionError):
            require_construction_activity_transition(current_status=current, next_status=next_status)

@pytest.mark.parametrize("current,next_status", [(None, "completed"), ("planned", "new"), ("deleted", "planned")])
def test_activity_unknown_status_is_validation_error(current, next_status):
    with pytest.raises(ConstructionActivityValidationError):
        require_construction_activity_transition(current_status=current, next_status=next_status)

@pytest.mark.parametrize("lead,client,project", [
    (True, False, False), (False, True, False), (False, False, True),
    (True, True, False), (True, False, True), (False, True, True), (True, True, True),
])
def test_activity_accepts_one_or_multiple_resource_references(lead, client, project):
    ids = [uuid4() if enabled else None for enabled in (lead, client, project)]
    links = ConstructionActivityLinks(lead_id=ids[0], client_id=ids[1], project_id=ids[2])
    assert (links.lead_id, links.client_id, links.project_id) == tuple(ids)
    with pytest.raises(FrozenInstanceError):
        links.project_id = uuid4()

def test_activity_requires_at_least_one_reference():
    with pytest.raises(ConstructionActivityValidationError):
        ConstructionActivityLinks()

@pytest.mark.parametrize("field", ["lead_id", "client_id", "project_id"])
def test_activity_does_not_treat_unparsed_text_as_uuid_reference(field):
    with pytest.raises(ConstructionActivityValidationError):
        ConstructionActivityLinks(**{field: str(uuid4())})

def test_activity_stores_absolute_deadline_as_utc_with_separate_display_timezone():
    deadline = datetime(2026, 10, 6, 14, 30, tzinfo=timezone(timedelta(hours=3)))
    schedule = normalize_construction_activity_schedule(due_at=deadline, timezone_code="Europe/Lisbon")
    assert schedule.due_at == datetime(2026, 10, 6, 11, 30, tzinfo=timezone.utc)
    assert schedule.due_at.tzinfo is timezone.utc
    assert schedule.timezone == "Europe/Lisbon"
    with pytest.raises(FrozenInstanceError):
        schedule.timezone = "UTC"

def test_activity_utc_input_is_independent_of_display_timezone_offset():
    deadline = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    schedule = normalize_construction_activity_schedule(due_at=deadline, timezone_code="Europe/Lisbon")
    assert schedule.due_at == deadline
    assert schedule.due_at.astimezone(ZoneInfo(schedule.timezone)).hour == 11

def test_activity_dst_fold_preserves_two_distinct_explicit_instants():
    zone = ZoneInfo("Europe/Lisbon")
    first = datetime(2026, 10, 25, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2026, 10, 25, 1, 30, tzinfo=zone, fold=1)
    one = normalize_construction_activity_schedule(due_at=first, timezone_code=zone.key)
    two = normalize_construction_activity_schedule(due_at=second, timezone_code=zone.key)
    assert two.due_at - one.due_at == timedelta(hours=1)

@pytest.mark.parametrize("deadline,zone", [
    (datetime(2026, 10, 6, 12), "Europe/Lisbon"),
    (None, "Europe/Lisbon"),
    ("2026-10-06T12:00:00Z", "Europe/Lisbon"),
    (datetime(2026, 10, 6, 12, tzinfo=timezone.utc), ""),
    (datetime(2026, 10, 6, 12, tzinfo=timezone.utc), "Europe/Unknown"),
    (datetime(2026, 10, 6, 12, tzinfo=timezone.utc), "../../etc/passwd"),
])
def test_activity_rejects_naive_deadline_and_invalid_timezone(deadline, zone):
    with pytest.raises(ConstructionActivityValidationError):
        normalize_construction_activity_schedule(due_at=deadline, timezone_code=zone)

def test_activity_schedule_cannot_bypass_utc_invariant():
    with pytest.raises(ConstructionActivityValidationError):
        ConstructionActivitySchedule(
            due_at=datetime(2026, 10, 6, 12, tzinfo=timezone(timedelta(hours=1))), timezone="Europe/Lisbon",
        )
