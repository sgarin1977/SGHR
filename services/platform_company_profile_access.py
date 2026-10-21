PLATFORM_ORGANIZATION_PROFILE_MANAGE = (
    "platform.organization.profile.manage"
)

_DEFAULT_GRANT_ROLES = frozenset({"OWNER", "ADMIN"})


def resolve_company_profile_permissions(
    *,
    actor,
    construction_access_context=None,
) -> frozenset[str]:
    if actor.status != "active":
        return frozenset()

    context = construction_access_context
    if context is not None and (
        context.user_id != actor.user_id
        or context.tenant_id != actor.tenant_id
    ):
        return frozenset()

    permissions = set()
    if PLATFORM_ORGANIZATION_PROFILE_MANAGE in actor.permissions:
        permissions.add(PLATFORM_ORGANIZATION_PROFILE_MANAGE)

    if context is not None:
        active_roles = set(context.roles)
        for grant in context.grants:
            if (
                grant.role in _DEFAULT_GRANT_ROLES
                and grant.role in active_roles
                and grant.scope_type == "tenant"
                and grant.scope_id == actor.tenant_id
            ):
                permissions.add(
                    PLATFORM_ORGANIZATION_PROFILE_MANAGE
                )

    return frozenset(permissions)


class CompanyProfilePermissionDeniedError(Exception):
    pass


def require_company_profile_manage(
    *,
    actor,
    construction_access_context=None,
) -> None:
    effective_permissions = resolve_company_profile_permissions(
        actor=actor,
        construction_access_context=construction_access_context,
    )
    if (
        PLATFORM_ORGANIZATION_PROFILE_MANAGE
        not in effective_permissions
    ):
        raise CompanyProfilePermissionDeniedError(
            "Company profile permission is not available."
        )


async def authorize_company_profile_write(
    *,
    actor,
    access_context_resolver=None,
):
    if PLATFORM_ORGANIZATION_PROFILE_MANAGE in actor.permissions:
        require_company_profile_manage(actor=actor)
        return None

    if access_context_resolver is None:
        raise CompanyProfilePermissionDeniedError(
            "Company profile permission is not available."
        )

    context = await access_context_resolver.resolve(actor=actor)
    require_company_profile_manage(
        actor=actor,
        construction_access_context=context,
    )
    return context


async def load_company_profile_write_context(*, actor):
    if actor.status != "active":
        raise CompanyProfilePermissionDeniedError(
            "Company profile permission is not available."
        )

    if PLATFORM_ORGANIZATION_PROFILE_MANAGE in actor.permissions:
        return await authorize_company_profile_write(actor=actor)

    from database.construction_session import construction_transaction
    from database.repositories.construction import (
        ConstructionAccessRepository,
    )
    from services.construction_grants import (
        ConstructionAccessContextResolver,
    )

    async with construction_transaction(
        tenant_id=actor.tenant_id,
    ) as session:
        resolver = ConstructionAccessContextResolver(
            repository=ConstructionAccessRepository(session),
        )
        return await authorize_company_profile_write(
            actor=actor,
            access_context_resolver=resolver,
        )
