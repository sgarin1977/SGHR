from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import (
    require_admin_any_permission,
)
from api.dependencies import (
    get_api_admin_audit_service,
)
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    AdminAuditListResponse,
)
from services.api_admin_audit import (
    ApiAdminAuditService,
)
from services.api_identity import (
    ApiActorContext,
)


from api.auth import require_admin_api_rate_limit


router = APIRouter(
    dependencies=[
        Depends(
            require_admin_api_rate_limit()
        ),
    ],
)

require_admin_audit_read = (
    require_admin_any_permission(
        "admin.logs.read",
    )
)


@router.get(
    "/admin/audit",
    response_model=AdminAuditListResponse,
)
async def list_admin_audit(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(require_admin_audit_read),
    ],
    service: Annotated[
        ApiAdminAuditService,
        Depends(get_api_admin_audit_service),
    ],
    actor_id: Annotated[
        UUID | None,
        Query(),
    ] = None,
    action: Annotated[
        list[str] | None,
        Query(),
    ] = None,
    object_type: Annotated[
        list[str] | None,
        Query(),
    ] = None,
    object_id: Annotated[
        UUID | None,
        Query(),
    ] = None,
    date_from: Annotated[
        datetime | None,
        Query(),
    ] = None,
    date_to: Annotated[
        datetime | None,
        Query(),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=100),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
):
    page = await service.list_audit(
        actor=actor,
        actor_user_id=actor_id,
        actions=tuple(action or ()),
        target_types=tuple(
            object_type or ()
        ),
        target_id=object_id,
        date_from=date_from,
        date_to=date_to,
        page=decode_page_cursor(cursor),
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "date": item.date,
                "actor": item.actor,
                "action": item.action,
                "target": item.target,
                "target_type": (
                    item.target_type
                ),
                "reason": item.reason,
                "source": item.source,
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
