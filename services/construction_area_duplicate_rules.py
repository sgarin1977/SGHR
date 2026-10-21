from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
import json
from uuid import UUID, uuid4

from services.construction_areas import (
    CONSTRUCTION_PROJECT_AREA_TYPES, CONSTRUCTION_PROJECT_AREA_STATUS_TRANSITIONS,
)
from services.construction_elements import (
    CONSTRUCTION_AREA_ELEMENT_TYPES, ConstructionElementParentError,
    validate_construction_geometry, validate_construction_parent_element,
)


class ConstructionAreaDuplicateValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ConstructionAreaDuplicateContext:
    """Typed context only; the service must verify tenant/project/measurement and access."""
    tenant_id: UUID
    project_id: UUID
    measurement_id: UUID

    def __post_init__(self):
        if any(not isinstance(value, UUID) for value in (self.tenant_id, self.project_id, self.measurement_id)):
            raise ConstructionAreaDuplicateValidationError("Duplicate context requires UUID references.")


@dataclass(frozen=True)
class ConstructionAreaDuplicateElement:
    source_element_id: UUID
    id: UUID
    parent_element_id: UUID | None
    element_type: str
    name: str
    sort_order: int | None
    geometry_json: dict
    row_version: int = field(init=False, default=1)


@dataclass(frozen=True)
class ConstructionAreaDuplicatePlan:
    source_area_id: UUID
    id: UUID
    context: ConstructionAreaDuplicateContext
    area_type: str
    name: str
    sort_order: int | None
    elements: tuple[ConstructionAreaDuplicateElement, ...]
    status: str = field(init=False, default="draft")
    row_version: int = field(init=False, default=1)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ConstructionAreaDuplicateValidationError("Duplicate names require nonblank text.")
    return value.strip()


def _sort_order(value):
    if value is not None and type(value) is not int:
        raise ConstructionAreaDuplicateValidationError("Duplicate sort order must be an integer or null.")
    return value


def _rows(value, label):
    if isinstance(value, (str, bytes, dict)) or not isinstance(value, Iterable):
        raise ConstructionAreaDuplicateValidationError("Duplicate " + label + " must be a collection.")
    return list(value)


def next_construction_area_copy_name(source_name, *, occupied_names):
    """Names must be loaded by the service from the selected measurement context."""
    base = _text(source_name) + " — копия"
    occupied = _rows(occupied_names, "occupied names")
    if any(not isinstance(value, str) for value in occupied):
        raise ConstructionAreaDuplicateValidationError("Duplicate occupied names must be text.")
    taken = set(occupied)
    if base not in taken:
        return base
    number = 2
    while base + " " + str(number) in taken:
        number += 1
    return base + " " + str(number)


def prepare_construction_area_duplicate(
    *, context, source_area, elements, occupied_names, uuid_provider=uuid4,
):
    """Pure preparation: no database, authorization, audit, measurement selection or commit."""
    if not isinstance(context, ConstructionAreaDuplicateContext):
        raise ConstructionAreaDuplicateValidationError("Duplicate requires a typed context.")
    context = ConstructionAreaDuplicateContext(context.tenant_id, context.project_id, context.measurement_id)
    if (
        not isinstance(getattr(source_area, "id", None), UUID)
        or getattr(source_area, "tenant_id", None) != context.tenant_id
        or getattr(source_area, "project_id", None) != context.project_id
        or getattr(source_area, "deleted_at", "missing") is not None
        or not isinstance(getattr(source_area, "area_type", None), str)
        or source_area.area_type not in CONSTRUCTION_PROJECT_AREA_TYPES
        or not isinstance(getattr(source_area, "status", None), str)
        or source_area.status not in CONSTRUCTION_PROJECT_AREA_STATUS_TRANSITIONS
    ):
        raise ConstructionAreaDuplicateValidationError("Duplicate source area is not available in the context.")
    area_name = next_construction_area_copy_name(source_area.name, occupied_names=occupied_names)
    area_sort = _sort_order(getattr(source_area, "sort_order", "missing"))
    if not callable(uuid_provider):
        raise ConstructionAreaDuplicateValidationError("Duplicate UUID provider must be callable.")
    records = _rows(elements, "elements")
    seen = set()
    active = []
    for element in records:
        identifier = getattr(element, "id", None)
        if (
            not isinstance(identifier, UUID) or identifier in seen
            or getattr(element, "tenant_id", None) != context.tenant_id
            or getattr(element, "project_area_id", None) != source_area.id
            or not hasattr(element, "deleted_at")
        ):
            raise ConstructionAreaDuplicateValidationError("Duplicate source element context is invalid.")
        seen.add(identifier)
        if element.deleted_at is None:
            active.append(element)
    active_by_id = {element.id: element for element in active}
    checked = []
    for element in active:
        if not isinstance(getattr(element, "element_type", None), str) or element.element_type not in CONSTRUCTION_AREA_ELEMENT_TYPES:
            raise ConstructionAreaDuplicateValidationError("Duplicate element type is invalid.")
        name = _text(getattr(element, "name", None))
        order = _sort_order(getattr(element, "sort_order", "missing"))
        geometry = deepcopy(validate_construction_geometry(getattr(element, "geometry_json", None)))
        # JSONB cannot store non-finite numbers or Python-only objects.
        try:
            json.dumps(geometry, allow_nan=False)
        except (ValueError, TypeError, OverflowError):
            raise ConstructionAreaDuplicateValidationError("Duplicate geometry must be valid JSON.") from None
        parent_id = getattr(element, "parent_element_id", "missing")
        if parent_id is not None:
            if not isinstance(parent_id, UUID) or parent_id not in active_by_id:
                raise ConstructionElementParentError("Duplicate parent must be an active copied element.")
            validate_construction_parent_element(
                element_type=element.element_type, tenant_id=context.tenant_id,
                project_area_id=source_area.id, parent_element=active_by_id[parent_id],
            )
        checked.append((element, name, order, geometry, parent_id))
    forbidden = seen | {source_area.id, context.tenant_id, context.project_id, context.measurement_id}
    allocated = set()
    def new_id():
        value = uuid_provider()
        if not isinstance(value, UUID) or value in forbidden or value in allocated:
            raise ConstructionAreaDuplicateValidationError("Duplicate UUID must be new and unique.")
        allocated.add(value)
        return value
    area_id = new_id()
    mapping = {element.id: new_id() for element in active}
    # Insert roots before children, retaining all stored sort_order values.
    checked.sort(key=lambda item: item[4] is not None)
    copied = tuple(ConstructionAreaDuplicateElement(
        source_element_id=element.id, id=mapping[element.id],
        parent_element_id=mapping[parent_id] if parent_id is not None else None,
        element_type=element.element_type, name=name, sort_order=order, geometry_json=geometry,
    ) for element, name, order, geometry, parent_id in checked)
    return ConstructionAreaDuplicatePlan(source_area_id=source_area.id, id=area_id, context=context,
        area_type=source_area.area_type, name=area_name, sort_order=area_sort, elements=copied)
