from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict

from api.auth import get_current_actor, require_module, require_suite
from api.responses import success_envelope
from api.routes.construction import (
    ConstructionAPIRouter, get_construction_scoped_session, require_construction_permission,
)
from api.routes.construction_access_mutations import _error
from services.construction_grants import ConstructionGrantOperationError
from services.construction_team_block_workflow import (
    PUBLIC_TEAM_BLOCK_FIELDS, build_construction_team_block_workflow,
)


class TeamBlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamBlockResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    revoked_grant_ids: list[UUID]


def get_team_block_workflow(session=Depends(get_construction_scoped_session)):
    return build_construction_team_block_workflow(session=session)


def _public(result, *, user_id):
    if (
        not isinstance(result, dict) or set(result) != PUBLIC_TEAM_BLOCK_FIELDS
        or not isinstance(result["revoked_grant_ids"], list)
    ):
        raise ConstructionGrantOperationError("Team block response is invalid.")
    item = TeamBlockResponse.model_validate(result)
    if (
        item.user_id != user_id
        or len(set(item.revoked_grant_ids)) != len(item.revoked_grant_ids)
        or item.revoked_grant_ids != sorted(item.revoked_grant_ids, key=lambda value: value.int)
    ):
        raise ConstructionGrantOperationError("Team block response is invalid.")
    return item.model_dump(mode="json")


access_permission = require_construction_permission("construction.access.manage")
router = ConstructionAPIRouter(
    prefix="/construction/team",
    dependencies=[
        Depends(get_current_actor),
        Depends(require_suite("construction", error_code="MODULE_DISABLED")),
        Depends(require_module("construction.core", error_code="MODULE_DISABLED")),
    ],
)


@router.post("/members/{user_id}/block", dependencies=[Depends(access_permission)])
async def block_member_construction_access(
    user_id: UUID, request: Request, body: TeamBlockRequest | None = None,
    actor=Depends(get_current_actor), workflow=Depends(get_team_block_workflow),
):
    try:
        result = await workflow.block_access(
            actor=actor, user_id=user_id, trace_id=request.state.request_id,
        )
        data = _public(result, user_id=user_id)
    except Exception as error:
        raise _error(error) from None
    return success_envelope(data=data, request_id=request.state.request_id)
