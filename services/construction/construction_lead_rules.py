from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID



CONSTRUCTION_LEAD_STATUSES = frozenset({
    "new", "contacted", "measurement_scheduled", "measurement_completed",
    "estimate_preparation", "proposal_sent", "negotiation", "won", "lost",
})
CONSTRUCTION_LEAD_TERMINAL_STATUSES = frozenset({"won", "lost"})
CONSTRUCTION_LEAD_STATUS_TRANSITIONS = MappingProxyType({
    "new": frozenset({"contacted", "lost"}),
    "contacted": frozenset({"measurement_scheduled", "lost"}),
    "measurement_scheduled": frozenset({"measurement_completed", "lost"}),
    "measurement_completed": frozenset({"estimate_preparation", "lost"}),
    "estimate_preparation": frozenset({"proposal_sent", "lost"}),
    "proposal_sent": frozenset({"negotiation", "lost"}),
    "negotiation": frozenset({"won", "lost"}),
    "won": frozenset(),
    "lost": frozenset(),
})


class ConstructionLeadValidationError(ValueError):
    pass


class ConstructionLeadTransitionError(ValueError):
    pass


def normalize_construction_lead_source(value):
    if not isinstance(value, str) or not value.strip():
        raise ConstructionLeadValidationError('Lead source is required.')
    return value.strip().lower()


def normalize_construction_lead_work_type_description(value):
    if not isinstance(value, str) or not value.strip():
        raise ConstructionLeadValidationError('Lead work description is required.')
    return value.strip()


def require_construction_lead_status(value):
    if not isinstance(value, str) or value not in CONSTRUCTION_LEAD_STATUSES:
        raise ConstructionLeadValidationError("Lead status is invalid.")


def require_construction_lead_transition(*, current_status, next_status):
    require_construction_lead_status(current_status)
    require_construction_lead_status(next_status)
    if next_status not in CONSTRUCTION_LEAD_STATUS_TRANSITIONS[current_status]:
        raise ConstructionLeadTransitionError("Lead status transition is not allowed.")


def _optional_text(value, *, field_name):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConstructionLeadValidationError("Lead " + field_name + " must be text.")
    return value.strip() or None


@dataclass(frozen=True)
class ConstructionLeadInput:
    source: str
    name: str
    work_type_description: str
    phone: str | None = None
    email: str | None = None
    address_raw: str | None = None
    comment: str | None = None
    responsible_user_id: UUID | None = None
    status: str = field(init=False, default="new")

    def __post_init__(self):
        source = normalize_construction_lead_source(self.source)
        work_type_description = normalize_construction_lead_work_type_description(self.work_type_description)
        name = _optional_text(self.name, field_name="name")
        if name is None:
            raise ConstructionLeadValidationError("Lead name is required.")
        values = {
            "source": source, 'work_type_description': work_type_description, "name": name,
            "phone": _optional_text(self.phone, field_name="phone"),
            "email": _optional_text(self.email, field_name="email"),
            "address_raw": _optional_text(self.address_raw, field_name="address"),
            "comment": _optional_text(self.comment, field_name="comment"),
        }
        if values["phone"] is None and values["email"] is None:
            raise ConstructionLeadValidationError("Lead requires phone or email.")
        if self.responsible_user_id is not None and not isinstance(self.responsible_user_id, UUID):
            raise ConstructionLeadValidationError("Lead responsible user must be a UUID.")
        for key, value in values.items():
            object.__setattr__(self, key, value)
