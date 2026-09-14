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
    get_api_admin_complaints_service,
)
from api.errors import ApiHttpError
from api.pagination import (
    decode_page_cursor,
    encode_page_cursor,
)
from api.responses import success_envelope
from api.schemas import (
    AdminComplaintDetailResponse,
    AdminComplaintListResponse,
    AdminComplaintModerationRequest,
    AdminComplaintModerationResponse,
)
from services.api_admin_complaints import (
    ApiAdminComplaintOperationError,
    ApiAdminComplaintsService,
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

require_admin_complaints_read = (
    require_admin_any_permission(
        "admin.complaints.manage",
        "moderation.complaints.view",
    )
)


require_admin_complaints_moderate = (
    require_admin_any_permission(
        "admin.complaints.manage",
        "moderation.complaints.resolve",
    )
)


@router.get(
    "/admin/complaints",
    response_model=AdminComplaintListResponse,
)
async def list_admin_complaints(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_complaints_read
        ),
    ],
    service: Annotated[
        ApiAdminComplaintsService,
        Depends(
            get_api_admin_complaints_service
        ),
    ],
    status: Annotated[
        list[str] | None,
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
    statuses = tuple(
        status
        or (
            "new",
            "in_review",
        )
    )

    page = await service.list_complaints(
        actor=actor,
        statuses=statuses,
        page=decode_page_cursor(cursor),
        page_size=limit,
    )

    return success_envelope(
        data=[
            {
                "id": item.id,
                "reporter_label": (
                    item.reporter_label
                ),
                "target_label": (
                    item.target_label
                ),
                "reason": item.reason,
                "status": item.status,
                "created_at": item.created_at,
                "is_assigned": (
                    item.is_assigned
                ),
                "has_conversation_context": (
                    item.has_conversation_context
                ),
                "requires_admin_escalation": (
                    item.requires_admin_escalation
                ),
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


@router.get(
    "/admin/complaints/{complaint_id}",
    response_model=AdminComplaintDetailResponse,
)
async def get_admin_complaint(
    complaint_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_complaints_read
        ),
    ],
    service: Annotated[
        ApiAdminComplaintsService,
        Depends(
            get_api_admin_complaints_service
        ),
    ],
):
    item = await service.get_complaint(
        actor=actor,
        complaint_id=complaint_id,
    )

    return success_envelope(
        data={
            "id": item.id,
            "reporter_label": (
                item.reporter_label
            ),
            "target_type": item.target_type,
            "target_label": item.target_label,
            "reason": item.reason,
            "comment": item.comment,
            "status": item.status,
            "created_at": item.created_at,
            "has_conversation_context": (
                item.has_conversation_context
            ),
            "requires_admin_escalation": (
                item.requires_admin_escalation
            ),
            "history": item.history,
        },
        request_id=request.state.request_id,
    )


async def _run_complaint_action(
    operation,
):
    try:
        return await operation
    except ApiAdminComplaintOperationError:
        raise ApiHttpError(
            status_code=422,
            code="complaint_moderation_failed",
            message=(
                "Complaint moderation could not "
                "be completed."
            ),
        ) from None


def _complaint_action_response(
    *,
    result,
    request: Request,
):
    return success_envelope(
        data={
            "complaint_id": (
                result.complaint_id
            ),
            "status": result.status,
            "message": result.message,
        },
        request_id=request.state.request_id,
    )


@router.post(
    "/admin/complaints/{complaint_id}/take",
    response_model=(
        AdminComplaintModerationResponse
    ),
)
async def take_admin_complaint(
    complaint_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_complaints_moderate
        ),
    ],
    service: Annotated[
        ApiAdminComplaintsService,
        Depends(
            get_api_admin_complaints_service
        ),
    ],
):
    result = await _run_complaint_action(
        service.take_complaint(
            actor=actor,
            complaint_id=complaint_id,
        )
    )
    return _complaint_action_response(
        result=result,
        request=request,
    )


@router.post(
    "/admin/complaints/{complaint_id}/escalate",
    response_model=(
        AdminComplaintModerationResponse
    ),
)
async def escalate_admin_complaint(
    complaint_id: UUID,
    payload: AdminComplaintModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_complaints_moderate
        ),
    ],
    service: Annotated[
        ApiAdminComplaintsService,
        Depends(
            get_api_admin_complaints_service
        ),
    ],
):
    result = await _run_complaint_action(
        service.escalate_complaint(
            actor=actor,
            complaint_id=complaint_id,
            reason=payload.reason,
        )
    )
    return _complaint_action_response(
        result=result,
        request=request,
    )


async def _finish_admin_complaint(
    *,
    complaint_id: UUID,
    status: str,
    payload: AdminComplaintModerationRequest,
    request: Request,
    actor: ApiActorContext,
    service: ApiAdminComplaintsService,
):
    result = await _run_complaint_action(
        service.resolve_complaint(
            actor=actor,
            complaint_id=complaint_id,
            status=status,
            reason=payload.reason,
        )
    )
    return _complaint_action_response(
        result=result,
        request=request,
    )


@router.post(
    "/admin/complaints/{complaint_id}/resolve",
    response_model=(
        AdminComplaintModerationResponse
    ),
)
async def resolve_admin_complaint(
    complaint_id: UUID,
    payload: AdminComplaintModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_complaints_moderate
        ),
    ],
    service: Annotated[
        ApiAdminComplaintsService,
        Depends(
            get_api_admin_complaints_service
        ),
    ],
):
    return await _finish_admin_complaint(
        complaint_id=complaint_id,
        status="resolved",
        payload=payload,
        request=request,
        actor=actor,
        service=service,
    )


@router.post(
    "/admin/complaints/{complaint_id}/reject",
    response_model=(
        AdminComplaintModerationResponse
    ),
)
async def reject_admin_complaint(
    complaint_id: UUID,
    payload: AdminComplaintModerationRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_admin_complaints_moderate
        ),
    ],
    service: Annotated[
        ApiAdminComplaintsService,
        Depends(
            get_api_admin_complaints_service
        ),
    ],
):
    return await _finish_admin_complaint(
        complaint_id=complaint_id,
        status="rejected",
        payload=payload,
        request=request,
        actor=actor,
        service=service,
    )
