from uuid import UUID

from fastapi import APIRouter, Depends, Request

from database.construction_session import (
    construction_transaction,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.dependencies import get_api_session, get_api_request_rate_limit_service
from services.api_identity import ApiActorContext
from services.construction_permissions import (
    CONSTRUCTION_PERMISSION_CODES,
    CONSTRUCTION_ROLE_PERMISSIONS,
    ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError,
)
from services.construction_grants import (
    build_construction_access_context_resolver,
)
from services.construction_team import (
    build_construction_team_access,
    build_construction_team_service,
)

from api.auth import (
    get_current_actor,
    require_authenticated_user_rate_limit,
    require_module,
    require_suite,
)




async def get_construction_scoped_session(
    actor: ApiActorContext = Depends(
        get_current_actor
    ),
):
    async with construction_transaction(
        tenant_id=actor.tenant_id,
    ) as session:
        yield session


async def get_construction_access_context(
    actor: ApiActorContext = Depends(get_current_actor),
    session=Depends(get_construction_scoped_session),
):
    resolver = build_construction_access_context_resolver(session)
    return await resolver.resolve(actor=actor)


def require_construction_permission(
    permission_code: str,
):
    normalized_permission = (
        permission_code.strip().lower()
        if isinstance(permission_code, str)
        else ""
    )

    if (
        normalized_permission
        not in CONSTRUCTION_PERMISSION_CODES
    ):
        raise ValueError(
            "A registered Construction "
            "permission is required."
        )

    async def dependency(
        actor: ApiActorContext = Depends(get_current_actor),
        access_context=Depends(get_construction_access_context),
    ):
        allowed = (
            actor.status == "active"
            and access_context.user_id == actor.user_id
            and access_context.tenant_id == actor.tenant_id
            and normalized_permission in access_context.permissions
            and any(
                grant.role in access_context.roles
                and normalized_permission
                in CONSTRUCTION_ROLE_PERMISSIONS.get(
                    grant.role,
                    frozenset(),
                )
                for grant in access_context.grants
            )
        )
        if not allowed:
            raise ApiHttpError(
                status_code=403,
                code="permission_required",
                message="Required permission is not available.",
            )
        return access_context

    dependency.construction_permission_code = normalized_permission
    return dependency


def _is_default_authenticated_rate_limit(dependency):
    return getattr(dependency.dependency, "api_rate_limit_context", None) == (
        "authenticated_user", get_current_actor, get_api_request_rate_limit_service)


class ConstructionAPIRouter(APIRouter):
    def __init__(self, *args, dependencies=None, **kwargs):
        # Authenticate/rate-limit before module gates and Construction sessions.
        # One guard per router; FastAPI caches it throughout each request.
        dependencies = [dependency for dependency in (dependencies or ())
                        if not _is_default_authenticated_rate_limit(dependency)]
        super().__init__(*args, dependencies=[
            Depends(require_authenticated_user_rate_limit()), *dependencies], **kwargs)

    def add_api_route(
        self,
        path,
        endpoint,
        *,
        dependencies=None,
        **kwargs,
    ):
        route_dependencies = list(
            dependencies or ()
        )

        has_construction_permission = any(
            getattr(
                dependency.dependency,
                "construction_permission_code",
                None,
            )
            for dependency in route_dependencies
        )

        if not has_construction_permission:
            raise ValueError(
                "Construction route permission "
                "is required."
            )

        # Existing explicit default guards would count the same bucket twice.
        # Keep custom principal/bucket guards; only remove the identical policy.
        route_dependencies = [dependency for dependency in route_dependencies
                              if not _is_default_authenticated_rate_limit(dependency)]
        responses = dict(kwargs.pop("responses", None) or {})
        responses.setdefault(429, {"description": "RATE_LIMIT_EXCEEDED"})
        return super().add_api_route(
            path,
            endpoint,
            dependencies=route_dependencies,
            responses=responses,
            **kwargs,
        )


router = ConstructionAPIRouter(
    prefix="/construction",
    dependencies=[
        Depends(get_current_actor),
        Depends(
            get_construction_scoped_session
        ),
        Depends(
            require_suite(
                "construction",
                error_code="MODULE_DISABLED",
            )
        ),
        Depends(
            require_module(
                "construction.core",
                error_code="MODULE_DISABLED",
            )
        ),
    ],
)


@router.get(
    "/team/access-grants",
    dependencies=[
        Depends(
            require_construction_permission(
                "construction.team.manage"
            )
        ),
    ],
)
async def list_team_access_grants(
    request: Request,
    actor: ApiActorContext = Depends(get_current_actor),
    session=Depends(get_construction_scoped_session),
    project_id: UUID | None = None,
):
    service, resolver = build_construction_team_access(session)
    access_context = await resolver.resolve(actor=actor)
    try:
        grants = await service.list_team_grants(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
        )
    except (
        ConstructionPermissionDeniedError,
        ConstructionScopeDeniedError,
    ) as exc:
        raise ApiHttpError(
            status_code=403,
            code="forbidden",
            message="Construction team access is not available.",
        ) from exc

    return success_envelope(
        data=[
            {
                "id": grant.id,
                "user_id": grant.user_id,
                "role": grant.role,
                "scope_type": grant.scope_type,
                "scope_id": grant.scope_id,
                "expires_at": grant.expires_at,
            }
            for grant in grants
        ],
        request_id=request.state.request_id,
    )

def get_team_member_service(
    construction_session=Depends(get_construction_scoped_session),
    platform_session=Depends(get_api_session),
):
    return build_construction_team_service(
        construction_session=construction_session,
        platform_session=platform_session,
    )


@router.get(
    "/team/members",
    dependencies=[
        Depends(require_construction_permission("construction.team.manage")),
    ],
)
async def list_team_members(
    request: Request,
    actor: ApiActorContext = Depends(get_current_actor),
    access_context=Depends(get_construction_access_context),
    service=Depends(get_team_member_service),
    project_id: UUID | None = None,
):
    try:
        members = await service.list_team_members(
            actor=actor,
            access_context=access_context,
            project_id=project_id,
        )
    except (
        ConstructionPermissionDeniedError,
        ConstructionScopeDeniedError,
    ) as exc:
        raise ApiHttpError(
            status_code=403,
            code="forbidden",
            message="Construction team access is not available.",
        ) from exc

    return success_envelope(
        data=[
            {
                "user_id": member["user_id"],
                "display_name": member["display_name"],
                "email": member["email"],
                "roles": member["roles"],
                "grants": [
                    {
                        "id": grant.id,
                        "role": grant.role,
                        "scope_type": grant.scope_type,
                        "scope_id": grant.scope_id,
                        "expires_at": grant.expires_at,
                    }
                    for grant in member["grants"]
                ],
                "scoped_permissions": member["scoped_permissions"],
            }
            for member in members
        ],
        request_id=request.state.request_id,
    )
