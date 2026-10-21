"""Operation-time client correlation from tenant-checked persisted resources.

Call inside the business transaction with the rows already validated/locked by
the operation. This helper performs no lookup, authorization or historical repair.
"""

from uuid import UUID

from services.construction_audit import ConstructionAuditContractError


_MISSING = object()
_ENTITY_TYPES = frozenset({
    "construction_client", "construction_lead", "construction_project",
    "construction_activity",
})


def _resource(row, *, tenant_id, resource_id=None):
    if (
        row is None
        or not isinstance(getattr(row, "id", None), UUID)
        or getattr(row, "tenant_id", None) != tenant_id
        or (resource_id is not None and row.id != resource_id)
    ):
        raise ConstructionAuditContractError("Audit correlation resource is invalid.")
    return row


def _reference(row, name):
    value = getattr(row, name, _MISSING)
    if value is not None and not isinstance(value, UUID):
        raise ConstructionAuditContractError("Audit correlation reference is invalid.")
    return value


def construction_audit_client_details(
    *, tenant_id: UUID, entity_type: str, entity,
    client=None, lead=None, project=None,
) -> dict[str, str]:
    """Return a new payload fragment only for a single known client.

    For activities, supply every linked client/lead/project row from the same
    operation transaction, including archived rows if the operation allows them.
    A missing/mismatched row is invalid evidence. Unknown or conflicting client
    ownership yields no singular marker; it is never guessed from one link.
    The returned UUID string is a snapshot, independent of later row changes.
    """
    if (not isinstance(tenant_id, UUID) or not isinstance(entity_type, str)
            or entity_type not in _ENTITY_TYPES):
        raise ConstructionAuditContractError("Audit correlation context is invalid.")
    _resource(entity, tenant_id=tenant_id)
    candidates = set()
    if entity_type != "construction_activity":
        if any(value is not None for value in (client, lead, project)):
            raise ConstructionAuditContractError("Unexpected audit correlation evidence.")
        if entity_type == "construction_client":
            candidates.add(entity.id)
        else:
            field = "converted_client_id" if entity_type == "construction_lead" else "client_id"
            value = _reference(entity, field)
            if value is not None:
                candidates.add(value)
    else:
        links = (
            ("client_id", client, None),
            ("lead_id", lead, "converted_client_id"),
            ("project_id", project, "client_id"),
        )
        linked = False
        for field, row, client_field in links:
            resource_id = _reference(entity, field)
            if resource_id is None:
                if row is not None:
                    raise ConstructionAuditContractError("Unexpected audit correlation evidence.")
                continue
            linked = True
            _resource(row, tenant_id=tenant_id, resource_id=resource_id)
            value = row.id if client_field is None else _reference(row, client_field)
            if value is not None:
                candidates.add(value)
        if not linked:
            raise ConstructionAuditContractError("Activity audit correlation requires a link.")
    if len(candidates) != 1:
        return {}
    return {"related_client_id": str(next(iter(candidates)))}
