"""Client for existing invitation and role-option APIs."""
from uuid import UUID

from services.construction.construction_api_client import ConstructionApiClientError


def problem(status=502, code="invalid_api_response"):
    return ConstructionApiClientError(status_code=status, code=code)


class ConstructionInvitationApiClient:
    def __init__(self, api):
        self.api = api

    @staticmethod
    def validate_item(item, *, session, project_id=None):
        scopes = [(scope.scope_type, scope.scope_id) for scope in item.scopes]
        if (
            not item.email.strip()
            or len(set(scopes)) != len(scopes)
            or any(kind == "tenant" and target != session.tenant_id for kind, target in scopes)
            or project_id is not None and not any(
                kind == "tenant" or kind == "project" and target == project_id
                for kind, target in scopes
            )
        ):
            raise ValueError("Invitation scope binding differs.")

    async def list(self, *, session, project_id=None):
        from api.routes.construction_invitation_list import InvitationListItem

        if project_id is not None and not isinstance(project_id, UUID):
            raise ValueError("Project UUID is required.")
        result = await self.api._get(
            "construction/invitations", session=session,
            params={"project_id": str(project_id)} if project_id is not None else None,
        )
        try:
            if not isinstance(result.data, list) or result.meta != {}:
                raise ValueError("Invitation list differs.")
            items = [InvitationListItem.model_validate(item) for item in result.data]
            if len({item.id for item in items}) != len(items):
                raise ValueError("Duplicate invitation.")
            for item in items:
                self.validate_item(item, session=session, project_id=project_id)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return [item.model_dump(mode="json") for item in items]

    async def role_options(self, *, session, project_id=None):
        from api.routes.construction_role_options import RoleOptionsResponse

        kind = "project" if project_id is not None else "tenant"
        target = project_id if project_id is not None else session.tenant_id
        if not isinstance(target, UUID):
            raise ValueError("Scope UUID is required.")
        result = await self.api._get(
            "construction/team/role-options", session=session,
            params={"scope_type": kind, "scope_id": str(target)},
        )
        try:
            item = RoleOptionsResponse.model_validate(result.data)
            if (
                result.meta != {}
                or result.data.get("permissions_read_only") is not True
                or item.scope_type != kind or item.scope_id != target
                or len({option.role for option in item.roles}) != len(item.roles)
                or any(
                    len(option.permissions) != len(set(option.permissions))
                    or any(not code.startswith("construction.") or code != code.strip()
                           for code in option.permissions)
                    for option in item.roles
                )
            ):
                raise ValueError("Role options binding differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return [option.role for option in item.roles]

    async def write(
        self, *, session, operation, payload, idempotency_key,
        invitation_id=None, project_id=None,
    ):
        from api.routes.construction_invitation_management import (
            InvitationCreateRequest, InvitationResendRequest,
            InvitationRevokeRequest, InvitationPublicResponse,
        )
        if (
            not isinstance(payload, dict)
            or not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 255
            or any(not 33 <= ord(char) <= 126 for char in idempotency_key)
        ):
            raise ValueError("Invitation request/key differs.")
        path = "construction/invitations"
        if operation == "create":
            if invitation_id is not None:
                raise ValueError("Creation cannot have an invitation ID.")
            body = InvitationCreateRequest.model_validate(payload)
            for scope in body.scopes:
                if scope.scope_type == "tenant" and scope.scope_id != session.tenant_id:
                    raise ValueError("Foreign tenant scope.")
        elif operation in ("resend", "revoke"):
            if not isinstance(invitation_id, UUID):
                raise ValueError("Invitation UUID is required.")
            schema = InvitationResendRequest if operation == "resend" else InvitationRevokeRequest
            body = schema.model_validate(payload)
            path += "/" + str(invitation_id) + "/" + operation
        else:
            raise ValueError("Unknown invitation operation.")
        result = await self.api._request(
            "POST", path, session=session,
            json_body=body.model_dump(mode="json", exclude_unset=True),
            idempotency_key=idempotency_key,
        )
        try:
            item = InvitationPublicResponse.model_validate(result.data)
            if result.meta != {}:
                raise ValueError("Invitation metadata differs.")
            self.validate_item(item, session=session, project_id=project_id)
            expected_status = "revoked" if operation == "revoke" else "pending"
            if item.status != expected_status:
                raise ValueError("Invitation status differs.")
            if invitation_id is not None and (
                item.id != invitation_id or item.row_version <= body.expected_row_version
            ):
                raise ValueError("Invitation receipt target/version differs.")
            if operation == "create" and (
                item.role != body.role
                or item.grant_expires_at != body.grant_expires_at
                or {(s.scope_type, s.scope_id) for s in item.scopes}
                != {(s.scope_type, s.scope_id) for s in body.scopes}
            ):
                raise ValueError("Created invitation scope differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise problem() from None
        return item.model_dump(mode="json")
