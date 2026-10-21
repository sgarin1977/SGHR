from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from services.construction_area_duplicate_rules import (
    ConstructionAreaDuplicateContext, ConstructionAreaDuplicateValidationError,
    next_construction_area_copy_name, prepare_construction_area_duplicate,
)
from services.construction_elements import ConstructionElementGeometryError, ConstructionElementParentError


@pytest.fixture
def duplicate_case():
    c = SimpleNamespace(tenant=uuid4(), project=uuid4(), measurement=uuid4())
    c.context = ConstructionAreaDuplicateContext(c.tenant, c.project, c.measurement)
    c.area = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, project_id=c.project,
        area_type="ROOM", name="Спальня", sort_order=None, status="confirmed", deleted_at=None,
        row_version=9, created_by=uuid4(), notes="private-measurement-comment",
        photos=[uuid4()], estimates=[uuid4()], audit_history=["private-history"])
    c.wall = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, project_area_id=c.area.id,
        parent_element_id=None, element_type="WALL", name="Стіна", sort_order=5,
        geometry_json={"schema_version": 1, "points": [[0, 0], [4, 0]], "custom": {"thickness": 0.12}},
        deleted_at=None, row_version=7, photo_file_id=uuid4(), measurement_result=42)
    c.window = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, project_area_id=c.area.id,
        parent_element_id=c.wall.id, element_type="WINDOW", name="Вікно", sort_order=5,
        geometry_json={"schema_version": 1, "custom": {"enabled": False, "labels": ["A"]}},
        deleted_at=None, row_version=3, note="private-window-note")
    c.archived = SimpleNamespace(id=uuid4(), tenant_id=c.tenant, project_area_id=c.area.id,
        parent_element_id=None, element_type="WALL", name="Архівна",
        sort_order=None, geometry_json={"invalid": "ignored archived data"},
        deleted_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
    c.elements = [c.window, c.archived, c.wall]
    return c


def prepare(c, **changes):
    data = dict(context=c.context, source_area=c.area, elements=c.elements, occupied_names=[])
    data.update(changes)
    return prepare_construction_area_duplicate(**data)


@pytest.mark.parametrize("area_type", ["ROOM", "PROJECT_GENERAL", "EXTERIOR", "OTHER"])
@pytest.mark.parametrize("status", ["draft", "measured", "confirmed"])
def test_copy_preserves_structure_resets_state_and_allocates_new_ids(duplicate_case, area_type, status):
    c = duplicate_case
    c.area.area_type, c.area.status = area_type, status
    before_area, before_elements = deepcopy(vars(c.area)), deepcopy([vars(e) for e in c.elements])
    plan = prepare(c)
    assert plan.source_area_id == c.area.id and plan.context == c.context
    assert plan.name == "Спальня — копия" and plan.area_type == area_type
    assert plan.sort_order is None and plan.status == "draft" and plan.row_version == 1
    assert len(plan.elements) == 2
    old = {c.area.id, *(e.id for e in c.elements)}
    new = {plan.id, *(e.id for e in plan.elements)}
    assert len(new) == 3 and not old & new
    assert [e.element_type for e in plan.elements] == ["WALL", "WINDOW"]
    root, child = plan.elements
    assert root.parent_element_id is None and child.parent_element_id == root.id
    assert root.source_element_id == c.wall.id and child.source_element_id == c.window.id
    assert root.name == c.wall.name and child.name == c.window.name
    assert root.sort_order == child.sort_order == 5
    assert root.row_version == child.row_version == 1
    assert root.geometry_json == c.wall.geometry_json and child.geometry_json == c.window.geometry_json
    assert vars(c.area) == before_area and [vars(e) for e in c.elements] == before_elements


@pytest.mark.parametrize("occupied,expected", [
    ([], "Кімната — копия"),
    (["Кімната — копия"], "Кімната — копия 2"),
    (["Кімната — копия", "Кімната — копия 2", "Кімната — копия 3"], "Кімната — копия 4"),
    (["Кімната — копия", "Кімната — копия 3"], "Кімната — копия 2"),
    (["Кімната", "Інша — копия"], "Кімната — копия"),
])
def test_name_uses_only_supplied_context_names(occupied, expected):
    assert next_construction_area_copy_name("Кімната", occupied_names=occupied) == expected


def test_new_copy_does_not_share_nested_geometry_with_source(duplicate_case):
    c = duplicate_case
    plan = prepare(c)
    root, child = plan.elements
    root.geometry_json["points"][0][0] = 99
    child.geometry_json["custom"]["labels"].append("B")
    assert c.wall.geometry_json["points"][0][0] == 0
    assert c.window.geometry_json["custom"]["labels"] == ["A"]
    c.wall.geometry_json["custom"]["thickness"] = 0.5
    assert root.geometry_json["custom"]["thickness"] == 0.12


def test_plan_whitelist_omits_photos_results_comments_and_history(duplicate_case):
    plan = prepare(duplicate_case)
    assert {f.name for f in fields(plan)} == {
        "source_area_id", "id", "context", "area_type", "name", "sort_order", "elements", "status", "row_version"}
    assert {f.name for f in fields(plan.elements[0])} == {
        "source_element_id", "id", "parent_element_id", "element_type", "name", "sort_order", "geometry_json", "row_version"}
    for item in (plan, *plan.elements):
        assert not any(name in vars(item) for name in (
            "created_by", "created_at", "updated_at", "deleted_at", "photos", "photo_file_id",
            "notes", "note", "measurement_result", "estimates", "audit_history"))


def test_immutable_metadata_and_context_have_no_direct_measurement_column_on_area(duplicate_case):
    plan = prepare(duplicate_case)
    with pytest.raises(FrozenInstanceError):
        plan.status = "confirmed"
    assert plan.context.measurement_id == duplicate_case.measurement
    assert not hasattr(plan, "measurement_id")


def test_empty_or_fully_archived_area_copies_with_no_elements(duplicate_case):
    c = duplicate_case
    assert prepare(c, elements=[]).elements == ()
    assert prepare(c, elements=[c.archived]).elements == ()


@pytest.mark.parametrize("field,value", [
    ("id", "unparsed"), ("tenant_id", uuid4()), ("project_id", uuid4()), ("deleted_at", True),
    ("area_type", "unknown"), ("area_type", []), ("status", "unknown"), ("status", []),
    ("name", " "), ("sort_order", True), ("sort_order", "1"),
])
def test_invalid_source_cannot_allocate_identifiers(duplicate_case, field, value):
    c = duplicate_case
    setattr(c.area, field, value)
    ids = Mock(side_effect=uuid4)
    with pytest.raises(ConstructionAreaDuplicateValidationError):
        prepare(c, uuid_provider=ids)
    ids.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("id", "unparsed"), ("tenant_id", uuid4()), ("project_area_id", uuid4()),
    ("element_type", "unknown"), ("element_type", []), ("name", " "),
    ("sort_order", False), ("sort_order", 1.5),
])
def test_invalid_active_element_fails_before_any_allocation(duplicate_case, field, value):
    c = duplicate_case
    setattr(c.window, field, value)
    ids = Mock(side_effect=uuid4)
    with pytest.raises(ConstructionAreaDuplicateValidationError):
        prepare(c, uuid_provider=ids)
    ids.assert_not_called()


@pytest.mark.parametrize("geometry,error", [
    ({}, ConstructionElementGeometryError), ([], ConstructionElementGeometryError),
    ({"schema_version": True}, ConstructionElementGeometryError),
    ({"schema_version": 2}, ConstructionElementGeometryError),
    ({"schema_version": 1, "value": float("nan")}, ConstructionAreaDuplicateValidationError),
    ({"schema_version": 1, "value": object()}, ConstructionAreaDuplicateValidationError),
])
def test_invalid_geometry_fails_without_partial_plan(duplicate_case, geometry, error):
    c = duplicate_case
    c.window.geometry_json = geometry
    ids = Mock(side_effect=uuid4)
    with pytest.raises(error):
        prepare(c, uuid_provider=ids)
    ids.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "archived", "wrong_parent_type", "wrong_child_type", "unparsed"])
def test_parent_must_be_an_active_copied_wall_in_same_area(duplicate_case, failure):
    c = duplicate_case
    if failure == "missing":
        c.window.parent_element_id = uuid4()
    elif failure == "archived":
        c.window.parent_element_id = c.archived.id
    elif failure == "wrong_parent_type":
        c.wall.element_type = "FLOOR"
    elif failure == "wrong_child_type":
        c.window.element_type = "NICHE"
    else:
        c.window.parent_element_id = "unparsed"
    ids = Mock(side_effect=uuid4)
    with pytest.raises(ConstructionElementParentError):
        prepare(c, uuid_provider=ids)
    ids.assert_not_called()


def test_repeated_source_identifier_cannot_create_ambiguous_remapping(duplicate_case):
    c = duplicate_case
    ids = Mock(side_effect=uuid4)
    with pytest.raises(ConstructionAreaDuplicateValidationError):
        prepare(c, elements=[c.wall, c.wall], uuid_provider=ids)
    ids.assert_not_called()


@pytest.mark.parametrize("failure", ["old_area", "old_element", "old_archived_element", "context", "duplicate", "unparsed"])
def test_new_ids_cannot_reuse_sources_context_or_other_allocations(duplicate_case, failure):
    c = duplicate_case
    repeated = uuid4()
    value = {
        "old_area": c.area.id, "old_element": c.wall.id, "old_archived_element": c.archived.id,
        "context": c.measurement, "duplicate": repeated, "unparsed": "unparsed",
    }[failure]
    provider = Mock(side_effect=[value, value, value])
    with pytest.raises(ConstructionAreaDuplicateValidationError):
        prepare(c, uuid_provider=provider)
    assert c.area.status == "confirmed" and c.window.parent_element_id == c.wall.id


@pytest.mark.parametrize("values", [
    {"elements": "bad"}, {"elements": None}, {"elements": {}},
    {"occupied_names": "bad"}, {"occupied_names": None}, {"occupied_names": [1]},
    {"uuid_provider": None}, {"context": None},
])
def test_invalid_input_collections_and_provider_are_rejected(duplicate_case, values):
    with pytest.raises(ConstructionAreaDuplicateValidationError):
        prepare(duplicate_case, **values)


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "measurement_id"])
def test_context_requires_typed_references(duplicate_case, field):
    c = duplicate_case
    values = dict(tenant_id=c.tenant, project_id=c.project, measurement_id=c.measurement)
    values[field] = "unparsed"
    with pytest.raises(ConstructionAreaDuplicateValidationError):
        ConstructionAreaDuplicateContext(**values)


def test_repeated_preparation_allocates_distinct_objects_without_mutating_source(duplicate_case):
    c = duplicate_case
    first = prepare(c)
    second = prepare(c, occupied_names=[first.name])
    assert second.name == "Спальня — копия 2"
    assert {first.id, *(e.id for e in first.elements)}.isdisjoint({second.id, *(e.id for e in second.elements)})
    assert c.area.name == "Спальня" and c.area.row_version == 9
