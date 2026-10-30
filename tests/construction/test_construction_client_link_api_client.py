"""Approved issuance/confirmation transport, secret handling and receipt binding."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from services.construction.construction_api_client import (
    ConstructionApiClient, ConstructionApiClientError, PlatformApiClientSession,
)
from services.construction.construction_bot_api_entry import ConstructionBotApiEntry
from services.construction.construction_client_link_api_client import (
    ClientLinkIssuedReceipt, ClientLinkRecipientMaterial, ClientLinkQueuedReceipt,
)


@pytest.fixture
def case():
    c = NS(actor=uuid4(), target=uuid4(), tenant=uuid4(), client=uuid4(), request=uuid4())
    c.token = "aB_7-" * 9 + "xyz"
    assert len(c.token) == 48
    c.context = "cclrc2_" + "local-test-recipient-context-" * 8
    # Historical timestamps intentionally verify that replay does not renew TTL.
    issued = datetime(2026, 1, 1, tzinfo=timezone.utc)
    c.issued = {
        "request_id": str(c.request), "tenant_id": str(c.tenant),
        "client_id": str(c.client), "target_user_id": str(c.target),
        "row_version": 1, "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(hours=24)).isoformat(),
        "recipient_context": c.context, "token": c.token,
    }
    c.queued = {
        name: value for name, value in c.issued.items()
        if name not in {"recipient_context", "token"}
    }
    c.queued["delivery_status"] = "queued"
    c.confirmed = {
        "request_id": str(c.request), "tenant_id": str(c.tenant),
        "client_id": str(c.client), "linked_user_id": str(c.target),
        "client_row_version": 8, "request_row_version": 2,
        "consumed_at": (issued + timedelta(hours=1)).isoformat(),
        "client_changed": True,
    }
    c.session = PlatformApiClientSession(c.actor, c.tenant, SecretStr("local-test-access"))
    return c


def response(data):
    return httpx.Response(200, json={"data": data, "meta": {}, "request_id": "link-transport"})


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
async def test_issue_uses_exact_version_target_previous_link_and_key(case, replacement):
    c = case
    previous = uuid4() if replacement else None
    calls = []
    def receive(request):
        calls.append(request)
        return response(c.queued)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        api = ConstructionApiClient(http=http)
        first = await api.request_client_user_link(
            session=c.session, client_id=c.client,
            target_email=" PERSON@EXAMPLE.COM ",
            expected_row_version=7, previous_user_id=previous,
            idempotency_key="same-issuance",
        )
        second = await api.request_client_user_link(
            session=c.session, client_id=c.client,
            target_email="person@example.com",
            expected_row_version=7, previous_user_id=previous,
            idempotency_key="same-issuance",
        )
    assert first == second
    assert first.expires_at - first.issued_at == timedelta(hours=24)
    assert first.delivery_status == "queued"
    assert not hasattr(first, "token") and not hasattr(first, "recipient_context")
    expected = {"target_email": "person@example.com", "expected_row_version": 7}
    if previous:
        expected["previous_user_id"] = str(previous)
    assert len(calls) == 2
    for request in calls:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/construction/clients/" + str(c.client) + "/user-link/requests"
        assert request.headers["idempotency-key"] == "same-issuance"
        assert json.loads(request.content) == expected


def test_secret_receipt_and_recipient_material_do_not_expose_secrets_in_repr_or_json(case):
    c = case
    item = ClientLinkIssuedReceipt.model_validate(c.issued)
    material = item.recipient_material()
    for value in (item, material):
        assert c.token not in repr(value) and c.context not in repr(value)
        assert c.token not in value.model_dump_json()
        assert c.context not in value.model_dump_json()
    assert isinstance(material.token, SecretStr)
    assert isinstance(material.recipient_context, SecretStr)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "tenant", "client", "target", "ttl", "version", "token", "naive",
])
async def test_issue_rejects_wrong_receipt_without_exposing_secrets(case, failure):
    c = case
    data = deepcopy(c.queued)
    if failure in ("tenant", "client"):
        data[failure + "_id"] = str(uuid4())
    elif failure == "target":
        data["target_user_id"] = str(c.actor)
    elif failure == "ttl":
        data["expires_at"] = "2026-01-01T00:15:00+00:00"
    elif failure == "version":
        data["row_version"] = 2
    elif failure == "token":
        data["token"] = c.token
    else:
        data["issued_at"] = "2026-01-01T00:00:00"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: response(data)),
    ) as http:
        with pytest.raises(ConstructionApiClientError) as caught:
            await ConstructionApiClient(http=http).request_client_user_link(
                session=c.session, client_id=c.client,
                target_email="person@example.com",
                expected_row_version=7, idempotency_key="issue",
            )
    assert c.token not in str(caught.value) and c.context not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [True, False])
async def test_confirm_sends_only_two_proof_fields_and_accepts_unrelated_session_tenant(case, changed):
    c = case
    material = ClientLinkIssuedReceipt.model_validate(c.issued).recipient_material()
    data = deepcopy(c.confirmed)
    data["client_changed"] = changed
    credential_tenant = uuid4()
    assert credential_tenant != c.tenant
    session = PlatformApiClientSession(c.target, credential_tenant, SecretStr("recipient-access"))
    calls = []
    def receive(request):
        calls.append(request)
        return response(data)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(receive),
    ) as http:
        result = await ConstructionApiClient(http=http).confirm_client_user_link(
            session=session, material=material, idempotency_key="same-confirmation",
        )
    assert result.client_changed is changed
    assert result.tenant_id == c.tenant and result.linked_user_id == c.target
    request = calls[0]
    assert len(calls) == 1 and request.method == "POST"
    assert request.url.path == "/api/v1/construction/client-user-link-requests/confirm"
    assert request.headers["idempotency-key"] == "same-confirmation"
    assert request.headers["authorization"] == "Bearer recipient-access"
    assert "x-tenant-id" not in request.headers
    assert json.loads(request.content) == {
        "recipient_context": c.context, "token": c.token,
    }


@pytest.mark.asyncio
async def test_foreign_recipient_is_rejected_before_http(case):
    c = case
    material = ClientLinkIssuedReceipt.model_validate(c.issued).recipient_material()
    calls = []
    def forbidden(request):
        calls.append(request)
        raise AssertionError("Wrong recipient must not submit.")
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(forbidden),
    ) as http:
        with pytest.raises(ValueError):
            await ConstructionApiClient(http=http).confirm_client_user_link(
                session=c.session, material=material, idempotency_key="confirmation",
            )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "request_id", "tenant_id", "client_id", "linked_user_id", "request_row_version",
])
async def test_confirmation_receipt_is_bound_to_original_material(case, field):
    c = case
    material = ClientLinkIssuedReceipt.model_validate(c.issued).recipient_material()
    data = deepcopy(c.confirmed)
    data[field] = 3 if field == "request_row_version" else str(uuid4())
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: response(data)),
    ) as http:
        with pytest.raises(ConstructionApiClientError):
            await ConstructionApiClient(http=http).confirm_client_user_link(
                session=PlatformApiClientSession(c.target, uuid4(), SecretStr("recipient-access")),
                material=material, idempotency_key="confirm",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "contains space", "x" * 256])
async def test_invalid_idempotency_key_never_sends_request(case, key):
    c = case
    calls = []
    def forbidden(request):
        calls.append(request)
        raise AssertionError("Invalid key must not be sent.")
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1", transport=httpx.MockTransport(forbidden),
    ) as http:
        with pytest.raises(ValueError):
            await ConstructionApiClient(http=http).request_client_user_link(
                session=c.session, client_id=c.client, target_email="person@example.com",
                expected_row_version=7, idempotency_key=key,
            )
    assert calls == []


@pytest.mark.asyncio
async def test_bot_issuance_entry_pins_identity_and_does_not_rebase_client_version(case):
    c = case
    receipt = ClientLinkQueuedReceipt.model_validate(c.queued)
    client = NS(request_client_user_link=AsyncMock(return_value=receipt))
    entry = ConstructionBotApiEntry(
        api=NS(construction=client), context=None, sessions=None,
    )
    entry._client_identity_session = AsyncMock(return_value=(c.session, "uk"))
    entry._presentation = AsyncMock(side_effect=AssertionError("No mutable-card rebase"))
    result = await entry.request_client_user_link(
        telegram_user_id=123, tenant_id=c.tenant, expected_user_id=c.actor,
        client_id=c.client, target_email="person@example.com",
        expected_row_version=7, previous_user_id=None,
        idempotency_key="original",
    )
    client.request_client_user_link.assert_awaited_once_with(
        session=c.session, client_id=c.client, target_email="person@example.com",
        expected_row_version=7, previous_user_id=None,
        idempotency_key="original",
    )
    entry._presentation.assert_not_awaited()
    assert result["request"] is receipt
    assert c.token not in repr(result) and c.context not in repr(result)

# client_link_email_http_v1
