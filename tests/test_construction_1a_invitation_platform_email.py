from dataclasses import replace
from datetime import timedelta
from html import unescape
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

from api.settings import ApiEmailDeliverySettings, ApiIdempotencySettings
from services.construction_invitation_platform_email import (
    ConstructionPlatformInvitationEmailDelivery, build_construction_platform_invitation_email_worker,
)
from services.construction_invitation_recipient_email import ConstructionInvitationRecipientEmailMaterial, ConstructionInvitationRecipientEmailSender
from services.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec
from services.construction_jobs import ConstructionJobPermanentError, ConstructionJobRetryError
from services.resend_email_provider import ResendEmailProvider
from test_construction_1a_invitation_delivery_loader import delivery

@pytest.fixture
def platform_email(delivery):
    state = delivery
    state.provider = SimpleNamespace(send_email=AsyncMock())
    state.request_codec = ConstructionInvitationRecipientContextCodec(encryption_key=state.key)
    state.adapter = ConstructionPlatformInvitationEmailDelivery(
        provider=state.provider, from_address="invite@example.com",
        recipient_page_url="https://app.example.com/construction/recipient",
        recipient_context_encryption_key=state.key, clock=lambda: state.now,
    )
    return state

async def material_for(state):
    material = await state.loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    request = state.request_codec.issue_for_delivery(material=material)
    return ConstructionInvitationRecipientEmailMaterial(
        invitation_id=material.invitation_id, tenant_id=material.tenant_id,
        email=material.email, expires_at=material.expires_at,
        token=request.token, invitation_context=request.invitation_context,
    )

@pytest.mark.asyncio
async def test_platform_provider_receives_escaped_message_with_fragment_link(platform_email):
    state = platform_email
    material = await material_for(state)
    await state.adapter.send_invitation(material=material, job_id=state.job.id)
    state.provider.send_email.assert_awaited_once()
    message = state.provider.send_email.await_args.kwargs
    assert set(message) == {"from_address", "to_address", "subject", "text_body", "html_body"}
    assert message["from_address"] == "invite@example.com" and message["to_address"] == material.email
    link = next(line for line in message["text_body"].splitlines() if line.startswith("https://"))
    parsed = urlsplit(link)
    assert parsed.query == "" and parsed.path == "/construction/recipient"
    values = parse_qs(parsed.fragment)
    assert set(values) == {"invitation_context", "token"}
    assert values["token"] == [material.token]
    context = state.request_codec.resolve(
        invitation_context=values["invitation_context"][0], token=values["token"][0],
    )
    assert context.tenant_id == state.tenant_id and context.invitation_id == state.invitation.id
    assert link in unescape(message["html_body"])
    assert "&amp;" in message["html_body"]
    assert material.expires_at.isoformat() in message["text_body"]
    assert str(state.tenant_id) not in message["text_body"] and str(state.job.id) not in message["text_body"]
    assert "verify the email" in message["text_body"]
    state.session.commit.assert_not_awaited()
    state.session.rollback.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("language,subject", [
    ("en", "SGHR Construction invitation"),
    ("pt", "Convite para o SGHR Construction"),
    ("ru", "Приглашение в SGHR Construction"),
    ("unconfigured", "SGHR Construction invitation"),
])
async def test_email_languages_and_english_fallback(platform_email, language, subject):
    state = platform_email
    adapter = ConstructionPlatformInvitationEmailDelivery(
        provider=state.provider, from_address="invite@example.com",
        recipient_page_url="https://app.example.com/construction/recipient",
        recipient_context_encryption_key=state.key, clock=lambda: state.now, language_code=language,
    )
    await adapter.send_invitation(material=await material_for(state), job_id=state.job.id)
    assert state.provider.send_email.await_args.kwargs["subject"] == subject

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "invitation", "token", "context", "expired", "email", "job"])
async def test_invalid_material_never_calls_email_provider(platform_email, failure):
    state = platform_email
    material = await material_for(state)
    job_id = state.job.id
    if failure == "tenant":
        material = replace(material, tenant_id=uuid4())
    elif failure == "invitation":
        material = replace(material, invitation_id=uuid4())
    elif failure == "token":
        material = replace(material, token="private-invalid-token")
    elif failure == "context":
        material = replace(material, invitation_context="private-invalid-context")
    elif failure == "expired":
        material = replace(material, expires_at=state.now)
    elif failure == "email":
        material = replace(material, email="invalid-email")
    else:
        job_id = "untrusted"
    with pytest.raises(ConstructionJobPermanentError) as caught:
        await state.adapter.send_invitation(material=material, job_id=job_id)
    assert caught.value.error_code == "INVITATION_DELIVERY_CONTEXT_INVALID"
    state.provider.send_email.assert_not_awaited()

@pytest.mark.parametrize("url", [
    "", "http://app.example.com/recipient", "javascript:alert(1)",
    "https://user:password@app.example.com/recipient",
    "https://app.example.com/recipient?token=secret",
    "https://app.example.com/recipient#secret",
    "https://app.example.com/recipient\n",
    "https://app.example.com:invalid/recipient",
])
def test_email_requires_explicit_safe_server_page_url(platform_email, url):
    state = platform_email
    with pytest.raises(ValueError):
        ConstructionPlatformInvitationEmailDelivery(
            provider=state.provider, from_address="invite@example.com",
            recipient_page_url=url, recipient_context_encryption_key=state.key,
            clock=lambda: state.now,
        )

@pytest.mark.asyncio
async def test_provider_failure_is_sanitized(platform_email):
    state = platform_email
    state.provider.send_email.side_effect = RuntimeError("private-token-and-provider-details")
    with pytest.raises(ConstructionJobPermanentError) as caught:
        await state.adapter.send_invitation(material=await material_for(state), job_id=state.job.id)
    assert caught.value.error_code == "EMAIL_DELIVERY_FAILED"
    assert "private-token" not in str(caught.value) and caught.value.__cause__ is None

@pytest.mark.asyncio
async def test_explicit_retry_survives_adapter(platform_email):
    state = platform_email
    error = ConstructionJobRetryError(
        error_code="EMAIL_PROVIDER_UNAVAILABLE", available_at=state.now + timedelta(minutes=1),
    )
    state.provider.send_email.side_effect = error
    with pytest.raises(ConstructionJobRetryError) as caught:
        await state.adapter.send_invitation(material=await material_for(state), job_id=state.job.id)
    assert caught.value is error

def configured_settings(state):
    email = ApiEmailDeliverySettings.from_env({
        "EMAIL_PROVIDER": "resend", "EMAIL_FROM": "invite@example.com",
        "WEB_APP_URL": "https://app.example.com", "RESEND_API_KEY": "re_test_secret_key",
    })
    crypto = ApiIdempotencySettings(encryption_key=state.key)
    return email, crypto

@pytest.mark.asyncio
async def test_factory_connects_worker_loader_context_and_platform_provider(platform_email):
    state = platform_email
    email, crypto = configured_settings(state)
    worker = build_construction_platform_invitation_email_worker(
        session=state.session, recipient_page_url="https://app.example.com/construction/recipient",
        lease_owner="mail-worker", lease_duration=timedelta(minutes=5), clock=lambda: state.now,
        email_settings=email, crypto_settings=crypto, provider=state.provider,
    )
    assert isinstance(worker.handlers["INVITATION_EMAIL"].send_invitation, ConstructionInvitationRecipientEmailSender)
    worker.queue = SimpleNamespace(
        claim_next=AsyncMock(return_value=state.job), mark_ready=AsyncMock(return_value=state.job.id),
        mark_failed=AsyncMock(), schedule_retry=AsyncMock(),
    )
    assert await worker.run_once() == state.job.id
    state.provider.send_email.assert_awaited_once()
    assert worker.queue.mark_ready.await_args.kwargs["result_reference"] == "construction_invitation:" + str(state.invitation.id)
    worker.queue.mark_failed.assert_not_awaited()
    assert state.token not in repr(worker.queue.mark_ready.await_args)
    state.session.commit.assert_not_awaited()
    state.session.rollback.assert_not_awaited()

def test_factory_rejects_foreign_frontend_origin(platform_email):
    state = platform_email
    email, crypto = configured_settings(state)
    with pytest.raises(ValueError, match="frontend origin"):
        build_construction_platform_invitation_email_worker(
            session=state.session, recipient_page_url="https://other.example.com/recipient",
            lease_owner="mail-worker", lease_duration=timedelta(minutes=5), clock=lambda: state.now,
            email_settings=email, crypto_settings=crypto, provider=state.provider,
        )

@pytest.mark.asyncio
async def test_existing_resend_provider_is_used_with_mock_transport(platform_email):
    state = platform_email
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "private-provider-id"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        provider = ResendEmailProvider(api_key="re_test_secret_key", client=client)
        adapter = ConstructionPlatformInvitationEmailDelivery(
            provider=provider, from_address="invite@example.com",
            recipient_page_url="https://app.example.com/construction/recipient",
            recipient_context_encryption_key=state.key, clock=lambda: state.now,
        )
        await adapter.send_invitation(material=await material_for(state), job_id=state.job.id)
    assert len(requests) == 1 and str(requests[0].url) == "https://api.resend.com/emails"
    assert requests[0].headers["Authorization"] == "Bearer re_test_secret_key"
