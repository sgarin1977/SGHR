from dataclasses import FrozenInstanceError, asdict
from itertools import product
from uuid import uuid4

import pytest

from services.construction_lead_rules import CONSTRUCTION_LEAD_STATUSES, CONSTRUCTION_LEAD_STATUS_TRANSITIONS, CONSTRUCTION_LEAD_TERMINAL_STATUSES, ConstructionLeadInput, ConstructionLeadTransitionError, ConstructionLeadValidationError, normalize_construction_lead_source, normalize_construction_lead_work_type_description, require_construction_lead_status, require_construction_lead_transition


SOURCES = (
    "website", "phone", "email", "whatsapp", "telegram", "social",
    "referral", "partner", "repeat_client", "walk_in", "manual", "other",
)
STATUSES = (
    "new", "contacted", "measurement_scheduled", "measurement_completed",
    "estimate_preparation", "proposal_sent", "negotiation", "won", "lost",
)
ALLOWED = {
    ("new", "contacted"), ("contacted", "measurement_scheduled"),
    ("measurement_scheduled", "measurement_completed"),
    ("measurement_completed", "estimate_preparation"),
    ("estimate_preparation", "proposal_sent"), ("proposal_sent", "negotiation"),
    ("negotiation", "won"),
    ("new", "lost"), ("contacted", "lost"), ("measurement_scheduled", "lost"),
    ("measurement_completed", "lost"), ("estimate_preparation", "lost"),
    ("proposal_sent", "lost"), ("negotiation", "lost"),
}


def lead(**changes):
    fields = dict(source="phone", name="Example client", work_type_description="Floor repair", phone="+351 123 456 789")
    fields.update(changes)
    return ConstructionLeadInput(**fields)


def test_lead_statuses_match_retained_lifecycle_contract():
    assert CONSTRUCTION_LEAD_STATUSES == frozenset(STATUSES)
    assert CONSTRUCTION_LEAD_TERMINAL_STATUSES == frozenset({'won', 'lost'})
    assert 'qualified' not in CONSTRUCTION_LEAD_STATUSES and 'proposal' not in CONSTRUCTION_LEAD_STATUSES
    with pytest.raises(TypeError):
        CONSTRUCTION_LEAD_STATUS_TRANSITIONS['won'] = frozenset({'new'})


@pytest.mark.parametrize("value", SOURCES)
def test_every_approved_source_is_available(value):
    assert normalize_construction_lead_source(value) == value
    assert lead(source=value).source == value


def test_source_case_and_surrounding_whitespace_normalize_to_known_code():
    assert normalize_construction_lead_source(" WEBSITE ") == "website"


@pytest.mark.parametrize('value', [None, True, 7, '', '   '])
def test_source_rejects_non_text_and_blank_values(value):
    with pytest.raises(ConstructionLeadValidationError):
        lead(source=value)


@pytest.mark.parametrize("value,expected", [
    ("  Ремонт підлоги  ", "Ремонт підлоги"), ("Drywall / Painting", "Drywall / Painting"),
    ("Mixed CASE and  spaces", "Mixed CASE and  spaces"),
    ("a618b2a0-d43f-4fc9-8820-9b0710c9165c", "a618b2a0-d43f-4fc9-8820-9b0710c9165c"),
])
def test_work_type_description_preserves_text_without_catalogue_conversion(value, expected):
    result = normalize_construction_lead_work_type_description(value)
    assert result == expected and isinstance(result, str)


@pytest.mark.parametrize("value", [None, True, 7, uuid4(), "", " \t\n "])
def test_work_type_description_requires_nonblank_text(value):
    with pytest.raises(ConstructionLeadValidationError):
        lead(work_type_description=value)


@pytest.mark.parametrize("value", [None, "", "   ", 123])
def test_name_is_required_nonblank_text(value):
    with pytest.raises(ConstructionLeadValidationError):
        lead(name=value)


@pytest.mark.parametrize("phone,email", [
    (None, None), ("", ""), ("   ", None), (None, "  "), (" \t ", " \n "),
])
def test_contact_requirement_is_checked_after_trim(phone, email):
    with pytest.raises(ConstructionLeadValidationError):
        lead(phone=phone, email=email)


@pytest.mark.parametrize("phone,email", [
    (" +351 123 456 789 ", None), (None, " Person@Example.com "),
    (" +351 123 456 789 ", " Person@Example.com "),
])
def test_phone_or_email_or_both_are_valid_presence_combinations(phone, email):
    result = lead(phone=phone, email=email)
    assert result.phone == (phone.strip() if phone else None)
    assert result.email == (email.strip() if email else None)


def test_lead_input_preserves_contact_case_optional_fields_and_server_initial_state():
    responsible = uuid4()
    result = lead(
        source=" OTHER ", name="  Client  Name  ", phone=" ", email=" Person@Example.com ",
        address_raw="  Rua Example 7  ", comment="  Source: local association  ",
        work_type_description="  Painting  ", responsible_user_id=responsible,
    )
    assert result.source == "other" and result.name == "Client  Name"
    assert result.phone is None and result.email == "Person@Example.com"
    assert result.address_raw == "Rua Example 7" and result.comment == "Source: local association"
    assert result.work_type_description == "Painting" and result.responsible_user_id == responsible
    assert result.status == "new" and asdict(result)["status"] == "new"
    with pytest.raises(FrozenInstanceError):
        result.status = "won"


def test_responsible_and_optional_address_comment_can_be_absent():
    result = lead(address_raw=" ", comment="\t")
    assert result.responsible_user_id is None and result.address_raw is None and result.comment is None


@pytest.mark.parametrize("field,value", [
    ("phone", 123), ("email", True), ("address_raw", {}), ("comment", []),
    ("responsible_user_id", "a618b2a0-d43f-4fc9-8820-9b0710c9165c"),
])
def test_lead_input_rejects_unparsed_reference_and_wrong_text_types(field, value):
    with pytest.raises(ConstructionLeadValidationError):
        lead(**{field: value})


@pytest.mark.parametrize("field", [
    "status", "tenant_id", "id", "row_version", "converted_client_id", "converted_project_id", "next_action",
])
def test_creation_input_does_not_accept_server_fields_conversion_or_next_action(field):
    with pytest.raises(TypeError):
        lead(**{field: "untrusted value"})


@pytest.mark.parametrize("current,next_status", list(product(STATUSES, repeat=2)))
def test_full_ordinary_lifecycle_allows_only_confirmed_forward_and_lost_transitions(current, next_status):
    if (current, next_status) in ALLOWED:
        assert require_construction_lead_transition(current_status=current, next_status=next_status) is None
    else:
        with pytest.raises(ConstructionLeadTransitionError):
            require_construction_lead_transition(current_status=current, next_status=next_status)


@pytest.mark.parametrize("value", [None, True, 123, "", "qualified", "proposal", "NEW", " contacted "])
def test_status_validation_does_not_revive_obsolete_or_unrecognized_codes(value):
    with pytest.raises(ConstructionLeadValidationError):
        require_construction_lead_status(value)


@pytest.mark.parametrize("current,next_status", [("qualified", "won"), ("new", "proposal"), (None, "lost")])
def test_transition_rejects_invalid_status_before_transition_logic(current, next_status):
    with pytest.raises(ConstructionLeadValidationError):
        require_construction_lead_transition(current_status=current, next_status=next_status)
