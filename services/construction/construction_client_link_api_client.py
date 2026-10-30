"""Client transport for the approved operator-link HTTP contracts.

Recipient metadata is an expected receipt context, not authorization.
The server authenticates the recipient_context and the target's Platform identity.
No delivery, identity lookup, session issuance or secret persistence is introduced.
"""
from datetime import timedelta
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt

from api.routes.construction_client_link_requests import (
    ClientLinkRequestBody, ClientLinkRequestIssuedResponse,
    ClientLinkRequestQueuedResponse,
)
from api.routes.construction_client_confirmation import (
    ClientConfirmationRequest, ClientConfirmationResponse,
)
from services.construction.construction_api_client import _response_error


class ClientLinkRecipientMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    tenant_id: UUID
    client_id: UUID
    target_user_id: UUID
    row_version: StrictInt = Field(ge=1, le=1)
    recipient_context: SecretStr = Field(min_length=1, max_length=4096, repr=False)
    token: SecretStr = Field(min_length=48, max_length=48, repr=False)


class ClientLinkQueuedReceipt(ClientLinkRequestQueuedResponse):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClientLinkIssuedReceipt(ClientLinkRequestIssuedResponse):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recipient_context: SecretStr = Field(min_length=1, max_length=4096, repr=False)
    token: SecretStr = Field(min_length=48, max_length=48, repr=False)

    def recipient_material(self):
        return ClientLinkRecipientMaterial(
            request_id=self.request_id, tenant_id=self.tenant_id,
            client_id=self.client_id, target_user_id=self.target_user_id,
            row_version=self.row_version,
            recipient_context=self.recipient_context, token=self.token,
        )


def key(value):
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 255
        or any(not 33 <= ord(char) <= 126 for char in value)
    ):
        raise ValueError("A valid idempotency key is required.")
    return value


def validate_secrets(material):
    token = material.token.get_secret_value()
    context = material.recipient_context.get_secret_value()
    if (
        re.fullmatch(r"[A-Za-z0-9_-]{48}", token) is None
        or not context.isascii() or context != context.strip()
    ):
        raise ValueError("Recipient material is invalid.")


class ConstructionClientLinkApiClient:
    def __init__(self, api):
        self.api = api

    async def issue(
        self, *, session, client_id, target_email,
        expected_row_version, idempotency_key, previous_user_id=None,
    ):
        if (
            not isinstance(client_id, UUID)
            or (
                previous_user_id is not None
                and not isinstance(previous_user_id, UUID)
            )
        ):
            raise ValueError("Canonical client and previous user UUIDs are required.")
        command = ClientLinkRequestBody(
            target_email=target_email,
            expected_row_version=expected_row_version,
            previous_user_id=previous_user_id,
        )
        result = await self.api._request(
            "POST", "construction/clients/" + str(client_id) + "/user-link/requests",
            session=session,
            json_body=command.model_dump(mode="json", exclude_none=True),
            idempotency_key=key(idempotency_key),
        )
        try:
            if (
                result.meta != {}
                or not isinstance(result.data, dict)
                or any(
                    not isinstance(result.data.get(name), str)
                    for name in ("issued_at", "expires_at")
                )
            ):
                raise ValueError("Issuance receipt is invalid.")
            item = ClientLinkQueuedReceipt.model_validate(result.data)
            if (
                item.tenant_id != session.tenant_id
                or item.client_id != client_id
                or item.target_user_id == session.user_id
                or item.expires_at - item.issued_at != timedelta(hours=24)
            ):
                raise ValueError("Issuance receipt context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        return item

    async def confirm(self, *, session, material, idempotency_key):
        if not isinstance(material, ClientLinkRecipientMaterial):
            raise ValueError("Recipient material is required.")
        validate_secrets(material)
        if material.target_user_id != session.user_id:
            raise ValueError("The authenticated recipient differs.")
        command = ClientConfirmationRequest(
            recipient_context=material.recipient_context, token=material.token,
        )
        # SecretStr JSON serialization masks secrets. Reveal them only for the
        # actual HTTPS/ASGI request body, never a state snapshot or log value.
        result = await self.api._request(
            "POST", "construction/client-user-link-requests/confirm",
            session=session,
            json_body={
                "recipient_context": command.recipient_context.get_secret_value(),
                "token": command.token.get_secret_value(),
            },
            idempotency_key=key(idempotency_key),
        )
        try:
            if not isinstance(result.data, dict) or not isinstance(result.data.get("consumed_at"), str):
                raise ValueError("Confirmation timestamp must be an ISO string.")
            item = ClientConfirmationResponse.model_validate(result.data)
            if (
                item.request_id != material.request_id
                or item.tenant_id != material.tenant_id
                or item.client_id != material.client_id
                or item.linked_user_id != session.user_id
                or item.request_row_version != material.row_version + 1
            ):
                raise ValueError("Confirmation receipt context differs.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _response_error() from None
        # Credential tenant does not establish membership in the client's tenant.
        return item

# client_link_email_http_v1
