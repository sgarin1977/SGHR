from datetime import timezone
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


class ActivityCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    type: Literal["call", "message", "meeting", "site_visit", "follow_up", "task", "other"]
    status: Literal["planned"]
    lead_id: UUID | None
    client_id: UUID | None
    project_id: UUID | None
    due_at: AwareDatetime
    timezone: StrictStr = Field(min_length=1)
    responsible_user_id: UUID | None
    comment: StrictStr | None
    result: None
    completed_at: None
    cancelled_at: None
    created_by_user_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime
    row_version: StrictInt = Field(ge=1, le=1)

    @field_validator("due_at", "created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.astimezone(timezone.utc)


PUBLIC_ACTIVITY_CREATED_FIELDS = frozenset(ActivityCreatedResponse.model_fields)


def public_created_activity(activity, *, tenant_id, activity_id):
    if (
        getattr(activity, "tenant_id", None) != tenant_id or getattr(activity, "id", None) != activity_id
        or getattr(activity, "deleted_at", "missing") is not None
    ):
        raise ValueError("Created activity response context is invalid.")
    return ActivityCreatedResponse.model_validate({
        field: getattr(activity, field) for field in PUBLIC_ACTIVITY_CREATED_FIELDS
    }).model_dump(mode="json")
