from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from services.construction.construction_lead_conversion_rules import (
    ConstructionLeadConversionSelection, inspect_construction_lead_conversion,
)
from services.construction.construction_lead_rules import (
    CONSTRUCTION_LEAD_STATUSES, CONSTRUCTION_LEAD_STATUS_TRANSITIONS,
    ConstructionLeadTransitionError, ConstructionLeadValidationError,
    require_construction_lead_transition,
)


NON_TERMINAL = (
    "new", "contacted", "measurement_scheduled", "measurement_completed",
    "estimate_preparation", "proposal_sent", "negotiation",
)


@pytest.mark.parametrize("status", NON_TERMINAL)
@pytest.mark.parametrize("scenario", ["new_client_new_project", "existing_client_new_project", "existing_client_existing_project"])
def test_each_approved_scenario_can_begin_from_each_non_terminal_status(status, scenario):
    client_id = None if scenario == "new_client_new_project" else uuid4()
    project_id = uuid4() if scenario == "existing_client_existing_project" else None
    selection = ConstructionLeadConversionSelection(client_id=client_id, project_id=project_id)
    state = inspect_construction_lead_conversion(status=status)
    assert state.is_replay is False
    assert state.converted_client_id is None and state.converted_project_id is None
    assert selection.client_id == client_id and selection.project_id == project_id
    assert selection.creates_client is (client_id is None)
    assert selection.creates_project is (project_id is None)


def test_new_client_cannot_be_attached_to_existing_project():
    with pytest.raises(ConstructionLeadValidationError):
        ConstructionLeadConversionSelection(project_id=uuid4())


@pytest.mark.parametrize("field", ["client_id", "project_id"])
@pytest.mark.parametrize("value", ["uuid-as-text", "", 17, True])
def test_selection_rejects_unparsed_or_invalid_resource_ids(field, value):
    data = dict(client_id=uuid4(), project_id=None)
    data[field] = value
    with pytest.raises(ConstructionLeadValidationError):
        ConstructionLeadConversionSelection(**data)


@pytest.mark.parametrize("with_links", [False, True])
def test_lost_does_not_reopen_automatically(with_links):
    links = dict(converted_client_id=uuid4(), converted_project_id=uuid4()) if with_links else {}
    with pytest.raises(ConstructionLeadTransitionError):
        inspect_construction_lead_conversion(status="lost", **links)


def test_won_returns_recorded_links_as_a_replay_without_new_targets():
    client_id, project_id = uuid4(), uuid4()
    state = inspect_construction_lead_conversion(
        status="won", converted_client_id=client_id, converted_project_id=project_id,
    )
    assert state.is_replay is True
    assert state.converted_client_id == client_id and state.converted_project_id == project_id


@pytest.mark.parametrize("field", ["converted_client_id", "converted_project_id"])
@pytest.mark.parametrize("value", [None, "", "uuid-as-text", 17, True])
def test_won_without_two_valid_recorded_links_cannot_invent_a_conversion(field, value):
    links = dict(converted_client_id=uuid4(), converted_project_id=uuid4())
    links[field] = value
    with pytest.raises(ConstructionLeadValidationError):
        inspect_construction_lead_conversion(status="won", **links)


@pytest.mark.parametrize("status", NON_TERMINAL)
@pytest.mark.parametrize("field", ["converted_client_id", "converted_project_id"])
def test_non_terminal_state_with_existing_links_cannot_start_another_conversion(status, field):
    with pytest.raises(ConstructionLeadValidationError):
        inspect_construction_lead_conversion(status=status, **{field: uuid4()})


@pytest.mark.parametrize("status", ["qualified", "proposal", "", "unknown", None, True])
def test_invalid_or_superseded_status_is_rejected(status):
    with pytest.raises(ConstructionLeadValidationError):
        inspect_construction_lead_conversion(status=status)


def test_conversion_does_not_relax_or_replace_ordinary_pipeline_transitions():
    assert set(NON_TERMINAL) | {"won", "lost"} == CONSTRUCTION_LEAD_STATUSES
    for status in NON_TERMINAL:
        assert inspect_construction_lead_conversion(status=status).is_replay is False
        if status != "negotiation":
            with pytest.raises(ConstructionLeadTransitionError):
                require_construction_lead_transition(current_status=status, next_status="won")
    require_construction_lead_transition(current_status="negotiation", next_status="won")
    assert CONSTRUCTION_LEAD_STATUS_TRANSITIONS["won"] == frozenset()
    assert CONSTRUCTION_LEAD_STATUS_TRANSITIONS["lost"] == frozenset()


def test_selection_and_inspection_result_are_immutable():
    selection = ConstructionLeadConversionSelection(client_id=uuid4())
    with pytest.raises(FrozenInstanceError):
        selection.project_id = uuid4()
    state = inspect_construction_lead_conversion(
        status="won", converted_client_id=uuid4(), converted_project_id=uuid4(),
    )
    with pytest.raises(FrozenInstanceError):
        state.converted_client_id = uuid4()
