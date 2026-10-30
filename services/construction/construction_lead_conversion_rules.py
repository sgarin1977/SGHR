from dataclasses import dataclass
from uuid import UUID

from services.construction.construction_lead_rules import (
    ConstructionLeadTransitionError, ConstructionLeadValidationError,
    require_construction_lead_status,
)


@dataclass(frozen=True)
class ConstructionLeadConversionSelection:
    """Selected IDs still require tenant, availability and ownership checks."""
    client_id: UUID | None = None
    project_id: UUID | None = None

    def __post_init__(self):
        for value in (self.client_id, self.project_id):
            if value is not None and not isinstance(value, UUID):
                raise ConstructionLeadValidationError("Conversion target must be a UUID.")
        if self.project_id is not None and self.client_id is None:
            raise ConstructionLeadValidationError("An existing project requires an existing client.")

    @property
    def creates_client(self):
        return self.client_id is None

    @property
    def creates_project(self):
        return self.project_id is None


@dataclass(frozen=True)
class ConstructionLeadConversionState:
    is_replay: bool
    converted_client_id: UUID | None
    converted_project_id: UUID | None


def inspect_construction_lead_conversion(
    *, status, converted_client_id=None, converted_project_id=None,
):
    """Inspect eligibility; mutation, locks, authorization and audit belong to the service."""
    require_construction_lead_status(status)
    if status == "lost":
        raise ConstructionLeadTransitionError("A lost lead requires an explicit reopen before conversion.")
    if status == "won":
        if not isinstance(converted_client_id, UUID) or not isinstance(converted_project_id, UUID):
            raise ConstructionLeadValidationError("Completed conversion links are not available.")
        return ConstructionLeadConversionState(True, converted_client_id, converted_project_id)
    if converted_client_id is not None or converted_project_id is not None:
        raise ConstructionLeadValidationError("An unconverted lead has unexpected conversion links.")
    return ConstructionLeadConversionState(False, None, None)
