from database.repositories.admin_scope import (
    AdminScopeContext,
)
from services.api_identity import (
    ApiActorContext,
)


ADMIN_API_ROLES = frozenset(
    {
        "moderator",
        "admin",
        "finance_admin",
        "super_admin",
    }
)


def build_api_admin_scope_context(
    actor: ApiActorContext,
) -> AdminScopeContext:
    actor_admin_roles = (
        ADMIN_API_ROLES.intersection(
            actor.roles
        )
    )

    is_global = (
        "super_admin" in actor_admin_roles
    )

    country_ids = {
        scope.scope_id
        for scope in actor.role_scopes
        if (
            scope.role in actor_admin_roles
            and scope.scope_type == "country"
            and scope.scope_id is not None
        )
    }

    language_codes = {
        scope.scope_code.strip().lower()
        for scope in actor.role_scopes
        if (
            scope.role in actor_admin_roles
            and scope.scope_type == "language"
            and scope.scope_code
            and scope.scope_code.strip()
        )
    }

    return AdminScopeContext(
        admin_user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        is_global=is_global,
        country_ids=frozenset(
            country_ids
        ),
        language_codes=frozenset(
            language_codes
        ),
    )
