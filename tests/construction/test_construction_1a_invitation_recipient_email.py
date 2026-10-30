from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from services.construction.construction_invitation_email_handler import (
    ConstructionInvitationEmailHandler, build_construction_invitation_email_worker,
)
from services.construction.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec
from services.construction.construction_invitation_recipient_email import (
    ConstructionInvitationRecipientEmailMaterial, ConstructionInvitationRecipientEmailSender,
)
from services.construction.construction_invitations import ConstructionInvitationTokenDeniedError, hash_construction_invitation_token
from services.construction.construction_jobs import ConstructionJobPermanentError, ConstructionJobRetryError
from test_construction_1a_invitation_delivery_loader import delivery

@pytest.fixture
def recipient_email(delivery):
    state = delivery
    state.recipient_key = Fernet.generate_key().decode("ascii")
    state.provider = AsyncMock(return_value="private-provider-response")
    state.sender = ConstructionInvitationRecipientEmailSender(
        send_invitation=state.provider, recipient_context_encryption_key=state.recipient_key,
    )
    state.handler = ConstructionInvitationEmailHandler(
        session=state.session, loader=state.loader, send_invitation=state.sender,
    )
    return state

def unchanged_transaction(state):
    state.session.commit.assert_not_awaited()
    state.session.rollback.assert_not_awaited()
    state.session.flush.assert_not_awaited()

@pytest.mark.asyncio
async def test_locked_loader_delivers_context_resolvable_by_recipient_api(recipient_email):
    state = recipient_email
    reference = await state.handler(state.job)
    assert reference == "construction_invitation:" + str(state.invitation.id)
    state.provider.assert_awaited_once()
    kwargs = state.provider.await_args.kwargs
    assert set(kwargs) == {"material", "job_id"} and kwargs["job_id"] == state.job.id
    material = kwargs["material"]
    assert isinstance(material, ConstructionInvitationRecipientEmailMaterial)
    assert material.email == state.invitation.email and material.token == state.token
    codec = ConstructionInvitationRecipientContextCodec(encryption_key=state.recipient_key)
    context = codec.resolve(invitation_context=material.invitation_context, token=material.token)
    assert context.tenant_id == state.invitation.tenant_id
    assert context.invitation_id == state.invitation.id
    assert state.token not in repr(material)
    assert material.invitation_context not in repr(material)
    assert state.token not in reference and "private-provider-response" not in reference
    assert len(state.statements) == 2
    unchanged_transaction(state)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rotated_token", "new_job", "accepted", "declined", "revoked", "expired", "used"])
async def test_stale_or_terminal_delivery_never_reaches_provider(recipient_email, failure):
    state = recipient_email
    if failure == "rotated_token":
        state.invitation.token_hash = hash_construction_invitation_token("private-replacement-token")
    elif failure == "new_job":
        state.invitation.delivery_metadata = {**state.invitation.delivery_metadata, "job_id": str(uuid4())}
    elif failure == "expired":
        state.invitation.expires_at = state.now
    elif failure == "used":
        state.invitation.token_used_at = state.now
    else:
        state.invitation.status = failure
    with pytest.raises(ConstructionJobPermanentError) as caught:
        await state.handler(state.job)
    assert caught.value.error_code == "INVITATION_DELIVERY_UNAVAILABLE"
    state.provider.assert_not_awaited()
    unchanged_transaction(state)

@pytest.mark.asyncio
async def test_resend_material_routes_only_with_new_token(recipient_email):
    state = recipient_email
    await state.handler(state.job)
    previous = state.provider.await_args.kwargs["material"]
    old_job = state.job
    new_token = "private-replacement-token"
    new_job = type(old_job)(
        id=uuid4(), tenant_id=old_job.tenant_id, user_id=old_job.user_id,
        resource_type=old_job.resource_type, resource_id=old_job.resource_id,
        job_type=old_job.job_type, status="RUNNING", lease_owner=old_job.lease_owner,
        lease_expires_at=old_job.lease_expires_at,
        idempotency_key="invitation:" + str(state.invitation.id) + ":" + hash_construction_invitation_token(new_token),
    )
    state.invitation.token_hash = hash_construction_invitation_token(new_token)
    state.invitation.delivery_metadata = {
        "job_id": str(new_job.id),
        "token_ciphertext": state.codec.encrypt(
            tenant_id=state.invitation.tenant_id, invitation_id=state.invitation.id, token=new_token,
        ),
    }
    state.provider.reset_mock()
    with pytest.raises(ConstructionJobPermanentError):
        await state.handler(old_job)
    state.provider.assert_not_awaited()
    state.job = new_job
    await state.handler(new_job)
    current = state.provider.await_args.kwargs["material"]
    codec = ConstructionInvitationRecipientContextCodec(encryption_key=state.recipient_key)
    assert codec.resolve(invitation_context=current.invitation_context, token=new_token).invitation_id == state.invitation.id
    for envelope, token in ((previous.invitation_context, new_token), (current.invitation_context, previous.token)):
        with pytest.raises(ConstructionInvitationTokenDeniedError):
            codec.resolve(invitation_context=envelope, token=token)
    unchanged_transaction(state)

@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ConstructionJobRetryError(
        error_code="EMAIL_PROVIDER_UNAVAILABLE",
        available_at=datetime(2026, 10, 6, 12, tzinfo=timezone.utc),
    ),
    ConstructionJobPermanentError(error_code="EMAIL_DELIVERY_FAILED"),
])
async def test_sender_preserves_explicit_provider_outcome(recipient_email, error):
    state = recipient_email
    material = await state.loader.load(tenant_id=state.tenant_id, job_id=state.job_id)
    state.provider.side_effect = error
    with pytest.raises(type(error)) as caught:
        await state.sender(material=material, job_id=state.job.id)
    assert caught.value is error
    unchanged_transaction(state)

@pytest.mark.asyncio
async def test_sender_rejects_invalid_material_before_provider(recipient_email):
    state = recipient_email
    with pytest.raises(ConstructionJobPermanentError) as caught:
        await state.sender(material=object(), job_id=state.job.id)
    assert caught.value.error_code == "INVITATION_DELIVERY_CONTEXT_INVALID"
    state.provider.assert_not_awaited()

def test_sender_rejects_unconfigured_crypto_and_callback():
    with pytest.raises(ValueError):
        ConstructionInvitationRecipientEmailSender(send_invitation=AsyncMock(), recipient_context_encryption_key="")
    with pytest.raises(ValueError):
        ConstructionInvitationRecipientEmailSender(send_invitation=None, recipient_context_encryption_key=Fernet.generate_key().decode("ascii"))

@pytest.mark.asyncio
@pytest.mark.parametrize("separate_key", [False, True])
async def test_worker_factory_wires_locked_loader_and_protected_material(recipient_email, separate_key):
    state = recipient_email
    options = {"recipient_context_encryption_key": state.recipient_key} if separate_key else {}
    worker = build_construction_invitation_email_worker(
        session=state.session, encryption_key=state.key, send_invitation=state.provider,
        lease_owner="mail-worker", lease_duration=timedelta(minutes=5),
        clock=lambda: state.now, **options,
    )
    handler = worker.handlers["INVITATION_EMAIL"]
    assert isinstance(handler.send_invitation, ConstructionInvitationRecipientEmailSender)
    assert handler.loader.session is state.session
    await handler(state.job)
    material = state.provider.await_args.kwargs["material"]
    key = state.recipient_key if separate_key else state.key
    codec = ConstructionInvitationRecipientContextCodec(encryption_key=key)
    assert codec.resolve(invitation_context=material.invitation_context, token=material.token).tenant_id == state.tenant_id
    unchanged_transaction(state)
