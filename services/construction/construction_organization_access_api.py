"""Tenant grant transport over the existing authorized Construction API."""
from uuid import UUID

from services.construction.construction_api_client import ConstructionApiResult, _response_error


async def role_options(client, *, session):
    from api.routes.construction_role_options import RoleOptionsResponse
    result = await client._get(
        "construction/team/role-options", session=session,
        params={"scope_type": "tenant", "scope_id": str(session.tenant_id)},
    )
    try:
        item = RoleOptionsResponse.model_validate(result.data)
        roles = [option.role for option in item.roles]
        if (
            result.data.get("permissions_read_only") is not True
            or item.scope_type != "tenant" or item.scope_id != session.tenant_id
            or len(set(roles)) != len(roles)
            or any(role not in ("OWNER", "ADMIN") for role in roles)
            or any(
                len(set(option.permissions)) != len(option.permissions)
                or any(
                    not code.startswith("construction.") or code != code.strip()
                    for code in option.permissions
                )
                for option in item.roles
            )
        ):
            raise ValueError("Organization role options differ.")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    return roles


async def create(client, *, session, target_user_id, role, expires_at, idempotency_key):
    from api.routes.construction_access_issuance import GrantCreateRequest
    from api.routes.construction_access_mutations import GrantMutationResponse
    if (
        not isinstance(target_user_id, UUID)
        or role not in ("OWNER", "ADMIN")
        or not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 255
        or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
    ):
        raise ValueError("Organization grant command is invalid.")
    command = GrantCreateRequest(
        role=role, scope_type="tenant", scope_id=session.tenant_id,
        expires_at=expires_at,
    )
    result = await client._request(
        "POST", "construction/team/members/" + str(target_user_id) + "/access-grants",
        session=session, json_body=command.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    try:
        item = GrantMutationResponse.model_validate(result.data)
        if (
            item.user_id != target_user_id or item.role != command.role
            or item.scope_type != "tenant" or item.scope_id != session.tenant_id
            or item.status != "active" or item.expires_at != command.expires_at
            or (item.expires_at is not None and item.expires_at <= item.granted_at)
        ):
            raise ValueError("Organization grant receipt differs.")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    return ConstructionApiResult(item.model_dump(mode="json"), result.meta, result.request_id)

async def replace(
    client, *, session, target_user_id, original_grant,
    role, expires_at, idempotency_key,
):
    from services.construction.construction_team_transport import Grant
    from api.routes.construction_access_mutations import (
        GrantReplaceRequest, GrantMutationResponse,
    )
    original = Grant.model_validate(original_grant)
    if (
        not isinstance(target_user_id, UUID)
        or original.scope_type != "tenant" or original.scope_id != session.tenant_id
        or original.role not in ("OWNER", "ADMIN") or role not in ("OWNER", "ADMIN")
        or not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 255
        or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
        or (
            original.expires_at is not None
            and (
                original.expires_at.tzinfo is None
                or original.expires_at.utcoffset() is None
            )
        )
    ):
        raise ValueError("Organization replacement context is invalid.")
    command = GrantReplaceRequest(
        role=role, scope_type="tenant", scope_id=session.tenant_id,
        expires_at=expires_at,
    )
    if command.role == original.role and command.expires_at == original.expires_at:
        raise ValueError("Organization access is unchanged.")
    result = await client._request(
        "POST", "construction/team/access-grants/" + str(original.id) + "/replace",
        session=session, json_body=command.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    try:
        item = GrantMutationResponse.model_validate(result.data)
        if (
            item.id == original.id or item.user_id != target_user_id
            or item.role != command.role or item.status != "active"
            or item.scope_type != "tenant" or item.scope_id != session.tenant_id
            or item.expires_at != command.expires_at
            or (item.expires_at is not None and item.expires_at <= item.granted_at)
        ):
            raise ValueError("Organization replacement receipt differs.")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    return ConstructionApiResult(item.model_dump(mode="json"), result.meta, result.request_id)

async def revoke(
    client, *, session, target_user_id, original_grant, idempotency_key,
):
    from services.construction.construction_team_transport import Grant
    from api.routes.construction_access_mutations import GrantMutationResponse
    original = Grant.model_validate(original_grant)
    if (
        not isinstance(target_user_id, UUID)
        or original.scope_type != "tenant" or original.scope_id != session.tenant_id
        or original.role not in ("OWNER", "ADMIN")
        or not isinstance(idempotency_key, str)
        or not 1 <= len(idempotency_key) <= 255
        or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
        or (
            original.expires_at is not None
            and (
                original.expires_at.tzinfo is None
                or original.expires_at.utcoffset() is None
            )
        )
    ):
        raise ValueError("Organization revocation context is invalid.")
    result = await client._request(
        "POST", "construction/team/access-grants/" + str(original.id) + "/revoke",
        session=session, json_body={}, idempotency_key=idempotency_key,
    )
    try:
        item = GrantMutationResponse.model_validate(result.data)
        if (
            item.id != original.id or item.user_id != target_user_id
            or item.role != original.role or item.status != "revoked"
            or item.scope_type != "tenant" or item.scope_id != session.tenant_id
            or item.expires_at != original.expires_at
            or (item.expires_at is not None and item.expires_at <= item.granted_at)
        ):
            raise ValueError("Organization revocation receipt differs.")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    return ConstructionApiResult(item.model_dump(mode="json"), result.meta, result.request_id)

def validate_block_data(data, *, target_user_id):
    from api.routes.construction_team_block import TeamBlockResponse
    item = TeamBlockResponse.model_validate(data)
    if (
        item.user_id != target_user_id
        or len(set(item.revoked_grant_ids)) != len(item.revoked_grant_ids)
        or item.revoked_grant_ids != sorted(item.revoked_grant_ids, key=lambda value: value.int)
    ):
        raise ValueError("Construction block receipt differs.")
    return item


async def block(client, *, session, target_user_id):
    if not isinstance(target_user_id, UUID):
        raise ValueError("Organization member UUID is required.")
    result = await client._request(
        "POST", "construction/team/members/" + str(target_user_id) + "/block",
        session=session, json_body={},
    )
    try:
        item = validate_block_data(result.data, target_user_id=target_user_id)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise _response_error() from None
    return ConstructionApiResult(item.model_dump(mode="json"), result.meta, result.request_id)
