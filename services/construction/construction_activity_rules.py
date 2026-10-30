from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from types import MappingProxyType
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ACTIVITY_RESULT_UNSET = object()

CONSTRUCTION_ACTIVITY_TYPES = frozenset({
    "call", "message", "meeting", "site_visit", "follow_up", "task", "other",
})
CONSTRUCTION_ACTIVITY_STATUSES = frozenset({"planned", "completed", "cancelled"})
CONSTRUCTION_ACTIVITY_STATUS_TRANSITIONS = MappingProxyType({
    "planned": frozenset({"completed", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
})

class ConstructionActivityValidationError(ValueError):
    pass

class ConstructionActivityTransitionError(ValueError):
    pass

def normalize_construction_activity_type(value):
    if not isinstance(value, str):
        raise ConstructionActivityValidationError("Activity type is invalid.")
    normalized = value.strip().lower()
    if normalized not in CONSTRUCTION_ACTIVITY_TYPES:
        raise ConstructionActivityValidationError("Activity type is invalid.")
    return normalized

def require_construction_activity_transition(*, current_status, next_status):
    if (
        not isinstance(current_status, str) or current_status not in CONSTRUCTION_ACTIVITY_STATUSES
        or not isinstance(next_status, str) or next_status not in CONSTRUCTION_ACTIVITY_STATUSES
    ):
        raise ConstructionActivityValidationError("Activity status is invalid.")
    if next_status not in CONSTRUCTION_ACTIVITY_STATUS_TRANSITIONS[current_status]:
        raise ConstructionActivityTransitionError("Activity status transition is not allowed.")

@dataclass(frozen=True)
class ConstructionActivityLinks:
    lead_id: UUID | None = None
    client_id: UUID | None = None
    project_id: UUID | None = None

    def __post_init__(self):
        values = (self.lead_id, self.client_id, self.project_id)
        if any(value is not None and not isinstance(value, UUID) for value in values):
            raise ConstructionActivityValidationError("Activity links must be UUID references.")
        if all(value is None for value in values):
            raise ConstructionActivityValidationError("Activity requires at least one linked resource.")

@dataclass(frozen=True)
class ConstructionActivitySchedule:
    due_at: datetime
    timezone: str

    def __post_init__(self):
        if (
            not isinstance(self.due_at, datetime)
            or self.due_at.tzinfo is not datetime_timezone.utc
            or not isinstance(self.timezone, str) or not self.timezone
        ):
            raise ConstructionActivityValidationError("Activity schedule must use UTC and an IANA timezone.")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ConstructionActivityValidationError("Activity timezone is invalid.") from None

def normalize_construction_activity_schedule(*, due_at, timezone_code):
    if (
        not isinstance(due_at, datetime) or due_at.tzinfo is None or due_at.utcoffset() is None
        or not isinstance(timezone_code, str) or not timezone_code.strip()
    ):
        raise ConstructionActivityValidationError("Activity requires an aware deadline and IANA timezone.")
    normalized_timezone = timezone_code.strip()
    try:
        ZoneInfo(normalized_timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ConstructionActivityValidationError("Activity timezone is invalid.") from None
    return ConstructionActivitySchedule(
        due_at=due_at.astimezone(datetime_timezone.utc), timezone=normalized_timezone,
    )
