from fastapi import APIRouter, Depends

from database.construction_session import (
    construction_transaction,
)
from services.api_identity import ApiActorContext

from api.auth import (
    get_current_actor,
    require_module,
    require_permission,
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


def require_construction_permission(
    permission_code: str,
):
    normalized_permission = (
        permission_code.strip().lower()
        if isinstance(permission_code, str)
        else ""
    )

    if not normalized_permission.startswith(
        "construction."
    ):
        raise ValueError(
            "Construction permission "
            "is required."
        )

    dependency = require_permission(
        normalized_permission
    )
    dependency.construction_permission_code = (
        normalized_permission
    )
    return dependency


class ConstructionAPIRouter(APIRouter):
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

        return super().add_api_route(
            path,
            endpoint,
            dependencies=route_dependencies,
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
