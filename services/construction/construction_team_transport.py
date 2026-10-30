"""Validation of the existing team API response; no authorization decisions."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictStr, TypeAdapter

Role = Literal[
    "OWNER", "ADMIN", "SURVEYOR", "ESTIMATOR",
    "PROJECT_MANAGER", "EXECUTOR", "OBSERVER",
]
Scope = Literal["tenant", "project", "document"]


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    role: Role
    scope_type: Scope
    scope_id: UUID
    expires_at: datetime | None


class PermissionScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grant_id: UUID
    role: Role
    scope_type: Scope
    scope_id: UUID
    permissions: list[StrictStr]


class Member(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    display_name: StrictStr | None
    email: StrictStr | None
    roles: list[Role]
    grants: list[Grant]
    scoped_permissions: list[PermissionScope]


def validate_team(data, *, tenant_id, project_id=None):
    if not isinstance(data, list):
        raise ValueError("A team array is required.")
    members = TypeAdapter(list[Member]).validate_python(data)
    users, grant_ids = set(), set()
    for member in members:
        if member.user_id in users or not member.grants:
            raise ValueError("Duplicate or empty team member.")
        users.add(member.user_id)
        if member.roles != sorted({grant.role for grant in member.grants}):
            raise ValueError("Team role aggregation differs.")
        if len(member.scoped_permissions) != len(member.grants):
            raise ValueError("Team permission scope count differs.")
        scopes = {}
        for item in member.scoped_permissions:
            if item.grant_id in scopes:
                raise ValueError("Duplicate permission scope.")
            if (
                len(set(item.permissions)) != len(item.permissions)
                or any(
                    not value.startswith("construction.") or value != value.strip()
                    for value in item.permissions
                )
            ):
                raise ValueError("Invalid permission projection.")
            scopes[item.grant_id] = item
        for grant in member.grants:
            if grant.id in grant_ids:
                raise ValueError("Duplicate team grant.")
            grant_ids.add(grant.id)
            if grant.scope_type == "tenant" and (
                grant.scope_id != tenant_id or grant.role not in ("OWNER", "ADMIN")
            ):
                raise ValueError("Foreign tenant scope.")
            if project_id is not None and (
                grant.scope_type == "document"
                or (
                    grant.scope_type == "project"
                    and grant.scope_id != project_id
                )
            ):
                raise ValueError("Foreign project scope.")
            if grant.expires_at is not None and (
                grant.expires_at.tzinfo is None
                or grant.expires_at.utcoffset() is None
            ):
                raise ValueError("Aware grant expiry required.")
            permission_scope = scopes.get(grant.id)
            if permission_scope is None or (
                permission_scope.role, permission_scope.scope_type,
                permission_scope.scope_id,
            ) != (grant.role, grant.scope_type, grant.scope_id):
                raise ValueError("Grant and permission scope differ.")
    return [member.model_dump(mode="json") for member in members]
