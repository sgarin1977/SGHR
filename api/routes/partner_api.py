from typing import Annotated
from uuid import UUID

from fastapi import Query
from fastapi import (
    APIRouter,
    Depends,
    Header,
    Request,
    status,
)

from api.auth import require_permission
from api.dependencies import (
    get_partner_api_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    PartnerApiClientCreateRequest,
    PartnerApiClientResponse,
)
from api.schemas import PartnerApiClientListResponse
from api.schemas import PartnerApiClientUpdateRequest
from services.api_idempotency import (
    ApiIdempotencyKeyReusedError,
)
from services.partner_api import (
    PartnerApiNotFoundError,
)
from api.schemas import (
    PartnerApiKeyCreateRequest,
    PartnerApiKeyIssuedResponse,
)
from api.schemas import PartnerApiKeyListResponse
from api.schemas import PartnerApiKeyRevokeResponse
from services.api_identity import (
    ApiActorContext,
)
from services.partner_api import (
    PartnerApiOperationError,
    PartnerApiService,
)


router = APIRouter()

require_api_clients_create = (
    require_permission(
        "api.clients.create"
    )
)


require_api_clients_update = (
    require_permission(
        "api.clients.update"
    )
)


require_api_clients_read = (
    require_permission(
        "api.clients.read"
    )
)


require_api_keys_create = (
    require_permission(
        "api.keys.create"
    )
)


require_api_keys_read = (
    require_permission(
        "api.keys.read"
    )
)


require_api_keys_revoke = (
    require_permission(
        "api.keys.revoke"
    )
)


@router.post(
    "/partner/api-clients",
    response_model=PartnerApiClientResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_partner_api_client(
    payload: PartnerApiClientCreateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_api_clients_create
        ),
    ],
    service: Annotated[
        PartnerApiService,
        Depends(get_partner_api_service),
    ],
):
    try:
        item = await service.create_api_client(
            tenant_id=actor.tenant_id,
            owner_type=payload.owner_type,
            owner_id=payload.owner_id,
            name=payload.name,
            metadata=payload.metadata,
        )
    except PartnerApiOperationError:
        raise ApiHttpError(
            status_code=422,
            code="api_client_create_failed",
            message=(
                "API client could not be "
                "created."
            ),
        ) from None

    return success_envelope(
        data={
            "id": item.id,
            "owner_type": item.owner_type,
            "owner_id": item.owner_id,
            "name": item.name,
            "status": item.status,
            "metadata": item.metadata,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        },
        request_id=request.state.request_id,
    )



@router.get(
    "/partner/api-clients",
    response_model=PartnerApiClientListResponse,
)
async def list_partner_api_clients(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_api_clients_read),
    ],
    service: Annotated[
        PartnerApiService,
        Depends(get_partner_api_service),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    page = await service.list_api_clients(
        tenant_id=actor.tenant_id,
        page=decode_page_cursor(cursor),
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "owner_type": item.owner_type,
                "owner_id": item.owner_id,
                "name": item.name,
                "status": item.status,
                "metadata": item.metadata,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in page.items
        ],
        meta={
            "next_cursor": (
                encode_page_cursor(
                    page.page + 1
                )
                if page.has_next
                else None
            ),
            "has_more": page.has_next,
        },
        request_id=request.state.request_id,
    )



@router.patch(
    "/partner/api-clients/{api_client_id}",
    response_model=PartnerApiClientResponse,
)
async def update_partner_api_client(
    api_client_id: UUID,
    payload: PartnerApiClientUpdateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_api_clients_update),
    ],
    service: Annotated[
        PartnerApiService,
        Depends(get_partner_api_service),
    ],
):
    try:
        item = await service.update_api_client(
            tenant_id=actor.tenant_id,
            api_client_id=api_client_id,
            name=payload.name,
            status=payload.status,
            metadata=payload.metadata,
        )
    except PartnerApiNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="api_client_not_found",
            message="API client was not found.",
        ) from None
    except PartnerApiOperationError:
        raise ApiHttpError(
            status_code=422,
            code="api_client_update_failed",
            message=(
                "API client could not be "
                "updated."
            ),
        ) from None

    return success_envelope(
        data={
            "id": item.id,
            "owner_type": item.owner_type,
            "owner_id": item.owner_id,
            "name": item.name,
            "status": item.status,
            "metadata": item.metadata,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        },
        request_id=request.state.request_id,
    )


@router.get(
    "/partner/api-clients/{api_client_id}",
    response_model=PartnerApiClientResponse,
)
async def get_partner_api_client(
    api_client_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_api_clients_read),
    ],
    service: Annotated[
        PartnerApiService,
        Depends(get_partner_api_service),
    ],
):
    try:
        item = await service.get_api_client(
            tenant_id=actor.tenant_id,
            api_client_id=api_client_id,
        )
    except PartnerApiNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="api_client_not_found",
            message="API client was not found.",
        ) from None

    return success_envelope(
        data={
            "id": item.id,
            "owner_type": item.owner_type,
            "owner_id": item.owner_id,
            "name": item.name,
            "status": item.status,
            "metadata": item.metadata,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        },
        request_id=request.state.request_id,
    )



@router.post(
    "/partner/api-clients/{api_client_id}/keys",
    response_model=PartnerApiKeyIssuedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_partner_api_key(
    api_client_id: UUID,
    payload: PartnerApiKeyCreateRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
        ),
    ],
    actor: Annotated[
        ApiActorContext,
        Depends(require_api_keys_create),
    ],
    service: Annotated[
        PartnerApiService,
        Depends(get_partner_api_service),
    ],
):
    try:
        item = await service.create_api_key(
            tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
            idempotency_key=idempotency_key,
            api_client_id=api_client_id,
            name=payload.name,
            expires_at=payload.expires_at,
            ip_allowlist=payload.ip_allowlist,
            scopes=tuple(payload.scopes),
        )
    except ApiIdempotencyKeyReusedError as exc:
        raise ApiHttpError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message=(
                "Idempotency key was reused with "
                "a different request."
            ),
        ) from exc
    except PartnerApiNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="api_client_not_found",
            message="API client was not found.",
        ) from None
    except PartnerApiOperationError:
        raise ApiHttpError(
            status_code=422,
            code="api_key_create_failed",
            message=(
                "API key could not be created."
            ),
        ) from None

    return success_envelope(
        data={
            "id": item.id,
            "api_client_id": (
                item.api_client_id
            ),
            "name": item.name,
            "key_prefix": item.key_prefix,
            "key": item.key,
            "environment": item.environment,
            "status": item.status,
            "expires_at": item.expires_at,
            "last_used_at": item.last_used_at,
            "ip_allowlist": item.ip_allowlist,
            "scopes": item.scopes,
            "created_at": item.created_at,
        },
        request_id=request.state.request_id,
    )



@router.get(
    "/partner/api-clients/{api_client_id}/keys",
    response_model=PartnerApiKeyListResponse,
)
async def list_partner_api_keys(
    api_client_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_api_keys_read),
    ],
    service: Annotated[
        PartnerApiService,
        Depends(get_partner_api_service),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    try:
        page = await service.list_api_keys(
            tenant_id=actor.tenant_id,
            api_client_id=api_client_id,
            page=decode_page_cursor(cursor),
            page_size=limit,
        )
    except PartnerApiNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="api_client_not_found",
            message="API client was not found.",
        ) from None

    return success_envelope(
        data=[
            {
                "id": item.id,
                "api_client_id": (
                    item.api_client_id
                ),
                "name": item.name,
                "key_prefix": item.key_prefix,
                "environment": item.environment,
                "status": item.status,
                "expires_at": item.expires_at,
                "last_used_at": (
                    item.last_used_at
                ),
                "ip_allowlist": (
                    item.ip_allowlist
                ),
                "scopes": item.scopes,
                "created_at": item.created_at,
            }
            for item in page.items
        ],
        meta={
            "next_cursor": (
                encode_page_cursor(
                    page.page + 1
                )
                if page.has_next
                else None
            ),
            "has_more": page.has_next,
        },
        request_id=request.state.request_id,
    )



@router.post(
    "/partner/api-keys/{api_key_id}/revoke",
    response_model=PartnerApiKeyRevokeResponse,
)
async def revoke_partner_api_key(
    api_key_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_api_keys_revoke),
    ],
    service: Annotated[
        PartnerApiService,
        Depends(get_partner_api_service),
    ],
):
    try:
        item = await service.revoke_api_key(
            tenant_id=actor.tenant_id,
            api_key_id=api_key_id,
        )
    except PartnerApiNotFoundError:
        raise ApiHttpError(
            status_code=404,
            code="api_key_not_found",
            message="API key was not found.",
        ) from None
    except PartnerApiOperationError:
        raise ApiHttpError(
            status_code=422,
            code="api_key_revoke_failed",
            message=(
                "API key could not be revoked."
            ),
        ) from None

    return success_envelope(
        data={
            "api_key_id": item.id,
            "status": item.status,
        },
        request_id=request.state.request_id,
    )
