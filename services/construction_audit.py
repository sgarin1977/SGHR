from dataclasses import dataclass
from typing import Any
from uuid import UUID


CONSTRUCTION_AUDIT_EVENT_TYPES = frozenset(
    {
        "construction_access_grant_created",
        "construction_access_grant_revoked",
        "construction_client_created",
        "construction_client_updated",
        "construction_client_archived",
        "construction_project_created",
        "construction_project_updated",
        "construction_project_status_changed",
        "construction_project_area_created",
        "construction_project_area_updated",
        "construction_project_area_status_changed",
        "construction_project_area_reopened",
        "construction_project_area_archived",
        "construction_area_element_created",
        "construction_area_element_updated",
        "construction_area_element_archived",
    }
)


class ConstructionAuditContractError(
    ValueError
):
    pass


@dataclass(frozen=True)
class ConstructionAuditChange:
    field: str
    old_value: Any
    new_value: Any

    def as_payload(self) -> dict:
        normalized_field = (
            self.field.strip()
            if isinstance(self.field, str)
            else ""
        )

        if not normalized_field:
            raise ConstructionAuditContractError(
                "Construction audit field "
                "is required."
            )

        return {
            "field": normalized_field,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


class ConstructionAuditLogger:
    def __init__(
        self,
        *,
        event_repository,
    ) -> None:
        self.event_repository = event_repository

    async def record_operation(
        self,
        *,
        actor,
        event_type: str,
        entity_type: str,
        entity_id: UUID,
        operation: str,
        changes: tuple[
            ConstructionAuditChange,
            ...,
        ] = (),
        details: dict[str, Any] | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
    ):
        normalized_event_type = (
            event_type.strip()
            if isinstance(event_type, str)
            else ""
        )
        normalized_entity_type = (
            entity_type.strip()
            if isinstance(entity_type, str)
            else ""
        )
        normalized_operation = (
            operation.strip()
            if isinstance(operation, str)
            else ""
        )

        if (
            normalized_event_type
            not in CONSTRUCTION_AUDIT_EVENT_TYPES
        ):
            raise ConstructionAuditContractError(
                "Construction audit event type "
                "is not allowed."
            )

        if not normalized_entity_type:
            raise ConstructionAuditContractError(
                "Construction audit entity type "
                "is required."
            )

        if not isinstance(entity_id, UUID):
            raise ConstructionAuditContractError(
                "Construction audit entity id "
                "is invalid."
            )

        if not normalized_operation:
            raise ConstructionAuditContractError(
                "Construction audit operation "
                "is required."
            )

        tenant_id = getattr(
            actor,
            "tenant_id",
            None,
        )
        user_id = getattr(
            actor,
            "user_id",
            None,
        )

        if (
            not isinstance(tenant_id, UUID)
            or not isinstance(user_id, UUID)
        ):
            raise ConstructionAuditContractError(
                "Verified Construction audit "
                "actor is required."
            )

        if details is None:
            normalized_details = {}
        elif isinstance(details, dict):
            normalized_details = dict(details)
        else:
            raise ConstructionAuditContractError(
                "Construction audit operation "
                "details are invalid."
            )

        reserved_payload_fields = {
            "operation",
            "changes",
            "reason",
        }
        if (
            reserved_payload_fields
            & normalized_details.keys()
        ):
            raise ConstructionAuditContractError(
                "Construction audit operation "
                "details contain reserved fields."
            )

        payload = {
            "operation": normalized_operation,
            "changes": [
                change.as_payload()
                for change in changes
            ],
            "reason": reason,
            **normalized_details,
        }

        return (
            await self.event_repository
            .create_event(
                event_type=(
                    normalized_event_type
                ),
                tenant_id=tenant_id,
                user_id=user_id,
                entity_type=(
                    normalized_entity_type
                ),
                entity_id=entity_id,
                payload=payload,
                platform="api",
                trace_id=trace_id,
            )
        )
