from uuid import UUID


class ConstructionTenantScopeError(
    PermissionError,
):
    pass


def require_construction_tenant_scope(
    *,
    actor_tenant_id: UUID | None,
    resource_tenant_id: UUID | None,
) -> UUID:
    if (
        actor_tenant_id is None
        or resource_tenant_id is None
        or actor_tenant_id
        != resource_tenant_id
    ):
        raise ConstructionTenantScopeError(
            "Construction resource is not "
            "available in the actor scope."
        )

    return actor_tenant_id
