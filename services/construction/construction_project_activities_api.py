"""Project activity GET transport; no repositories or write services."""
from uuid import UUID

from api.construction_project_activity_cursors import decode_cursor
from services.construction.construction_activity_read import ActivityPlannedReadResponse
from services.construction.construction_api_client import ConstructionApiResult, _response_error


async def read(client, *, session, project_id, limit=20, cursor=None):
    if not isinstance(project_id, UUID) or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Project activity context is invalid.")
    after = decode_cursor(
        cursor, tenant_id=session.tenant_id, user_id=session.user_id, project_id=project_id,
    )
    params = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    result = await client._get(
        "construction/projects/" + str(project_id) + "/activities",
        session=session, params=params,
    )
    try:
        if (
            not isinstance(result.data, list) or len(result.data) > limit
            or not isinstance(result.meta.get("pagination"), dict)
            or set(result.meta["pagination"]) != {"next_cursor"}
        ):
            raise ValueError("Project activity response is invalid.")
        items, seen = [], set()
        previous = (after.due_at, after.activity_id) if after else None
        for raw in result.data:
            item = ActivityPlannedReadResponse.model_validate(raw)
            position = item.due_at, item.id
            if (
                item.tenant_id != session.tenant_id or item.project_id != project_id
                or item.id in seen or (previous is not None and position <= previous)
            ):
                raise ValueError("Project activity receipt differs.")
            seen.add(item.id)
            previous = position
            items.append(item.model_dump(mode="json"))
        token = result.meta["pagination"]["next_cursor"]
        next_position = decode_cursor(
            token, tenant_id=session.tenant_id, user_id=session.user_id, project_id=project_id,
        )
        if next_position is not None and (
            len(items) != limit or not items
            or (next_position.due_at, next_position.activity_id) != previous
        ):
            raise ValueError("Project activity cursor differs.")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    return ConstructionApiResult(
        items, {"pagination": {"next_cursor": token}}, result.request_id,
    )
