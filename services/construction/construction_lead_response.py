from datetime import timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


class LeadCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    source: StrictStr = Field(min_length=1)
    name: StrictStr = Field(min_length=1)
    work_type_description: StrictStr = Field(min_length=1)
    phone: StrictStr | None
    email: StrictStr | None
    address_raw: StrictStr | None
    comment: StrictStr | None
    responsible_user_id: UUID | None
    status: Literal["new"]
    converted_client_id: None
    converted_project_id: None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: StrictInt = Field(ge=1, le=1)

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)


PUBLIC_LEAD_CREATED_FIELDS = frozenset(LeadCreatedResponse.model_fields)


def public_created_lead(lead, *, tenant_id, lead_id):
    if (
        getattr(lead, "tenant_id", None) != tenant_id
        or getattr(lead, "id", None) != lead_id
        or getattr(lead, "deleted_at", "missing") is not None
    ):
        raise ValueError("Created lead response context is invalid.")
    return LeadCreatedResponse.model_validate({
        field: getattr(lead, field) for field in PUBLIC_LEAD_CREATED_FIELDS
    }).model_dump(mode="json")
