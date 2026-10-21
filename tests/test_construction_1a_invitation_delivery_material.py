from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from database.models import ConstructionInvitation
from database.repositories.construction import ConstructionInvitationRepository
from services.construction_invitation_delivery import (
    ConstructionInvitationDeliveryQueue,
    ConstructionInvitationDeliveryTokenError,
    ConstructionInvitationTokenCodec,
)
from services.construction_invitation_delivery_material import (
    prepare_construction_invitation_delivery,
)
from services.construction_invitations import hash_construction_invitation_token


def claim(job, now):
    job.status = "RUNNING"
    job.attempts = 1
    job.lease_owner = "mail-worker"
    job.lease_expires_at = now + timedelta(minutes=5)


@pytest_asyncio.fixture
async def delivery_case():
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    token = "private-current-invitation-token"
    actor = SimpleNamespace(
        tenant_id=uuid4(), user_id=uuid4(), status="active",
    )
    invitation = ConstructionInvitation(
        id=uuid4(), tenant_id=actor.tenant_id,
        email="person@example.com", role="ADMIN", status="pending",
        created_by_user_id=uuid4(), user_id=None,
        expires_at=now + timedelta(days=7), grant_expires_at=None,
        token_hash=hash_construction_invitation_token(token),
        token_used_at=None, delivery_metadata={"attempt_count": 0},
        row_version=4,
    )
    session = SimpleNamespace(
        flush=AsyncMock(),
        commit=AsyncMock(side_effect=AssertionError("Must not commit.")),
    )
    repository = ConstructionInvitationRepository(session)
    jobs = []

    async def enqueue(*, job):
        jobs.append(job)
        return job.id

    codec = ConstructionInvitationTokenCodec(
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    adapter = ConstructionInvitationDeliveryQueue(
        session=session,
        queue=SimpleNamespace(session=session, enqueue=enqueue),
        invitation_repository=repository,
        token_codec=codec,
        now_provider=lambda: now,
    )
    await adapter.enqueue_invitation(
        actor=actor, invitation=invitation, token=token,
    )
    job = jobs[-1]
    claim(job, now)
    decrypt = Mock(wraps=codec.decrypt)

    return SimpleNamespace(
        now=now, token=token, actor=actor, invitation=invitation,
        session=session, repository=repository, jobs=jobs, job=job,
        codec=codec, adapter=adapter, decrypt=decrypt,
        reader=SimpleNamespace(decrypt=decrypt),
    )


def prepare(case, job=None):
    return prepare_construction_invitation_delivery(
        job=case.job if job is None else job,
        invitation=case.invitation,
        token_codec=case.reader,
        lease_owner="mail-worker",
        as_of=case.now,
    )


def state(case):
    return (
        {
            column.name: deepcopy(getattr(case.invitation, column.name))
            for column in case.invitation.__table__.columns
        },
        {
            column.name: deepcopy(getattr(case.job, column.name))
            for column in case.job.__table__.columns
        },
    )


def reject_before_decryption(case):
    before = state(case)
    with pytest.raises(ConstructionInvitationDeliveryTokenError):
        prepare(case)
    case.decrypt.assert_not_called()
    assert state(case) == before
    case.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_claimed_job_prepares_private_material_without_mutation(
    delivery_case,
):
    case = delivery_case
    before = state(case)

    material = prepare(case)

    assert material.invitation_id == case.invitation.id
    assert material.tenant_id == case.actor.tenant_id
    assert material.email == "person@example.com"
    assert material.token == case.token
    assert material.expires_at == case.invitation.expires_at
    assert case.token not in repr(material)
    assert case.invitation.token_hash not in repr(material)
    assert case.invitation.delivery_metadata["token_ciphertext"] not in repr(material)
    assert state(case) == before
    # Resend може виконати інший уповноважений співробітник.
    assert case.job.user_id != case.invitation.created_by_user_id
    case.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resend_rejects_old_job_and_allows_only_latest_token(delivery_case):
    case = delivery_case
    new_token = "private-new-token-after-resend"

    await case.repository.resend_invitation(
        invitation=case.invitation,
        token_hash=hash_construction_invitation_token(new_token),
        expires_at=case.now + timedelta(days=7),
        delivery_metadata={"attempt_count": 0},
    )
    await case.adapter.enqueue_invitation(
        actor=case.actor, invitation=case.invitation, token=new_token,
    )

    reject_before_decryption(case)
    latest = case.jobs[-1]
    assert latest.id != case.job.id
    claim(latest, case.now)

    material = prepare(case, job=latest)
    assert material.token == new_token
    assert material.token != case.token
    case.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", [
    "accepted", "declined", "revoked", "expired",
    "expired_pending", "expiry_boundary", "used_token",
])
async def test_terminal_expired_or_used_invitation_is_not_decrypted(
    delivery_case, condition,
):
    case = delivery_case
    if condition in {"accepted", "declined", "revoked", "expired"}:
        case.invitation.status = condition
    elif condition == "expired_pending":
        case.invitation.expires_at = case.now - timedelta(seconds=1)
    elif condition == "expiry_boundary":
        case.invitation.expires_at = case.now
    else:
        case.invitation.token_used_at = case.now
    reject_before_decryption(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", [
    "foreign_tenant", "foreign_invitation", "wrong_job_type", "wrong_resource_type",
])
async def test_foreign_or_unrelated_job_is_not_decrypted(delivery_case, condition):
    case = delivery_case
    if condition == "foreign_tenant":
        case.job.tenant_id = uuid4()
    elif condition == "foreign_invitation":
        case.job.resource_id = uuid4()
    elif condition == "wrong_job_type":
        case.job.job_type = "EXPORT_PDF"
    else:
        case.job.resource_type = "construction_project"
    reject_before_decryption(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", [
    "queued", "wrong_owner", "expired_lease", "naive_lease",
])
async def test_job_without_current_worker_lease_is_not_decrypted(
    delivery_case, condition,
):
    case = delivery_case
    if condition == "queued":
        case.job.status = "QUEUED"
    elif condition == "wrong_owner":
        case.job.lease_owner = "another-worker"
    elif condition == "expired_lease":
        case.job.lease_expires_at = case.now
    else:
        case.job.lease_expires_at = case.now.replace(tzinfo=None)
    reject_before_decryption(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", [
    "invalid_ciphertext", "foreign_ciphertext", "wrong_token",
])
async def test_invalid_encrypted_material_fails_without_exposing_secrets(
    delivery_case, condition,
):
    case = delivery_case
    if condition == "invalid_ciphertext":
        ciphertext = "invalid-encrypted-material"
    else:
        ciphertext = case.codec.encrypt(
            tenant_id=(
                uuid4() if condition == "foreign_ciphertext"
                else case.invitation.tenant_id
            ),
            invitation_id=case.invitation.id,
            token=(
                "private-wrong-token" if condition == "wrong_token"
                else case.token
            ),
        )
    case.invitation.delivery_metadata["token_ciphertext"] = ciphertext
    before = state(case)

    with pytest.raises(ConstructionInvitationDeliveryTokenError) as caught:
        prepare(case)

    case.decrypt.assert_called_once()
    error = str(caught.value) + repr(caught.value)
    for secret in (
        case.token, "private-wrong-token", ciphertext, case.invitation.token_hash,
    ):
        assert secret not in error
    assert caught.value.__cause__ is None
    assert state(case) == before
    case.session.commit.assert_not_awaited()
