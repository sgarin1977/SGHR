"""Internal Platform bot adapter for ownership-only client unlink.

Uses the same trusted dispatch identity as organization bootstrap.
No tenant session is issued. Client ownership comes from the existing RLS
lookup, and the existing unlink workflow rechecks ownership/version.
"""
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ConfigDict

from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.platform_bot_context import require_platform_bot_context
from api.responses import success_envelope
from api.routes.platform_bot_entry import (
    failure as bot_failure,
    get_platform_bot_rates,
    get_platform_bot_users,
    require_canonical_bot_user,
)
from api.routes.construction_client_own_link_state import (
    ClientOwnLinkStateEnvelope, ClientOwnLinkStateResponse,
)
from api.routes.construction_client_unlink import (
    ClientUnlinkRequest, ClientUnlinkEnvelope, ClientUnlinkRoute,
    public_unlink, unlink_errors,
)
from database.construction_client_unlink_context import (
    construction_client_unlink_lookup_transaction,
)
from database.construction_session import construction_transaction
from services.api_identity import ApiActorContext
from services.api_platform_principal import ApiPlatformPrincipal
from services.construction.construction_client_own_link_state import (
    build_construction_client_own_link_state_service,
)
from services.construction.construction_client_unlink_context import (
    build_construction_client_own_link_context_service,
)
from services.construction.construction_client_unlink_workflow import (
    build_construction_client_unlink_workflow,
)


class BotOwnerUnlinkRequest(ClientUnlinkRequest):
    model_config = ConfigDict(extra="forbid")
    expected_user_id: UUID


@dataclass(frozen=True)
class BotOwnerUnlinkInput:
    principal: ApiPlatformPrincipal
    client_id: UUID
    expected_row_version: int


async def require_bot_link_user(
    context=Depends(require_platform_bot_context),
    users=Depends(get_platform_bot_users),
    rates=Depends(get_platform_bot_rates),
):
    try:
        return await require_canonical_bot_user(context, users, rates)
    except Exception as error:
        raise bot_failure(error) from None


def expected_identity(user, expected_user_id):
    # The supplied UUID is a comparison selector, never an identity source.
    if user.id != expected_user_id:
        raise ApiHttpError(
            status_code=403, code="account_unavailable",
            message="Platform account is unavailable.",
        )
    return ApiPlatformPrincipal(user.id)


def query_fields(request, allowed):
    if (
        set(request.query_params) != set(allowed)
        or any(len(request.query_params.getlist(key)) != 1 for key in allowed)
    ):
        raise ApiHttpError(
            status_code=422, code="validation_error",
            message="Own-link query is invalid.",
        )


router = APIRouter(
    prefix="/platform/bot/client-links",
    include_in_schema=False,
    dependencies=[Depends(require_platform_bot_context)],
)


@router.get("/{id}/self", response_model=ClientOwnLinkStateEnvelope)
async def read_bot_own_link(
    id: UUID, request: Request, response: Response,
    expected_user_id: UUID = Query(),
    user=Depends(require_bot_link_user),
    platform_session=Depends(get_api_session),
):
    query_fields(request, {"expected_user_id"})
    principal = expected_identity(user, expected_user_id)
    with unlink_errors():
        async with construction_client_unlink_lookup_transaction(
            user_id=principal.user_id, client_id=id,
        ) as session:
            service = build_construction_client_own_link_state_service(
                session=session, platform_session=platform_session,
            )
            state = await service.get_state(principal=principal, client_id=id)
        item = ClientOwnLinkStateResponse.model_validate({
            field: getattr(state, field)
            for field in ClientOwnLinkStateResponse.model_fields
        })
        if item.client_id != id or item.linked_user_id != principal.user_id:
            raise ValueError("Own-link response context differs.")
    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=item.model_dump(mode="json"),
        meta={"actor_user_id": str(principal.user_id)},
        request_id=request.state.request_id,
    )


@router.post("/{id}/unlink", response_model=ClientUnlinkEnvelope)
async def unlink_bot_own_link(
    id: UUID, body: BotOwnerUnlinkRequest,
    request: Request, response: Response,
    user=Depends(require_bot_link_user),
    platform_session=Depends(get_api_session),
):
    query_fields(request, set())
    principal = expected_identity(user, body.expected_user_id)
    with unlink_errors():
        # End the dedicated read-only lookup before opening the write session.
        async with construction_client_unlink_lookup_transaction(
            user_id=principal.user_id, client_id=id,
        ) as session:
            resolver = build_construction_client_own_link_context_service(
                session=session, platform_session=platform_session,
            )
            own = await resolver.resolve_own_link(
                principal=principal, client_id=id,
            )
        if (
            own.user_id != principal.user_id or own.client_id != id
            or not isinstance(own.tenant_id, UUID)
        ):
            raise ValueError("Own-link routing context differs.")

        actor = ApiActorContext(
            principal.user_id, None, None, (), "ru", None, "active",
        )
        route = ClientUnlinkRoute(actor, own.tenant_id, id, False)
        inputs = BotOwnerUnlinkInput(principal, id, body.expected_row_version)

        async with construction_transaction(tenant_id=own.tenant_id) as session:
            workflow = build_construction_client_unlink_workflow(
                session=session, platform_session=platform_session,
            )
            result = await workflow.unlink_self(
                actor=actor, tenant_id=own.tenant_id, client_id=id,
                expected_row_version=body.expected_row_version,
                idempotency_key=None, trace_id=request.state.request_id,
            )
            data = public_unlink(result, route=route, inputs=inputs)

    response.headers["Cache-Control"] = "private, no-store"
    return success_envelope(
        data=data, meta={"actor_user_id": str(principal.user_id)},
        request_id=request.state.request_id,
    )
