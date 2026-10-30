from typing import Literal
from uuid import UUID

from fastapi import Depends, Request
from api.auth import get_current_actor, require_module, require_suite
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_access_context, get_construction_scoped_session,
)
from api.routes.construction_invitation_management import InvitationPublicResponse, manage_permission, _error
from services.construction.construction_invitation_list import build_construction_invitation_list_service
from services.construction.construction_invitations import ConstructionInvitationOperationError

class InvitationListItem(InvitationPublicResponse):
    status: Literal["pending", "accepted", "declined", "expired", "revoked"]

def get_invitation_list_service(session=Depends(get_construction_scoped_session)):
    return build_construction_invitation_list_service(session=session)

router = ConstructionAPIRouter(
    prefix="/construction/invitations",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)

@router.get("", dependencies=[Depends(manage_permission)])
async def list_invitations(
    request: Request, project_id: UUID | None = None,
    actor=Depends(get_current_actor), access_context=Depends(get_construction_access_context),
    service=Depends(get_invitation_list_service),
):
    try:
        items = await service.list_invitations(actor=actor, access_context=access_context, project_id=project_id)
        keys = {"id", "email", "role", "status", "scopes", "expires_at", "grant_expires_at", "row_version"}
        if not isinstance(items, list) or any(not isinstance(item, dict) or set(item) != keys for item in items):
            raise ConstructionInvitationOperationError("Invitation list response is invalid.")
        data = [InvitationListItem.model_validate(item).model_dump(mode="json") for item in items]
    except Exception as exc:
        raise _error(exc) from None
    return success_envelope(data=data, request_id=request.state.request_id)
