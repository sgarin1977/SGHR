from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

from database.repositories.job_queue import JobLeaseLostError
from services.construction.construction_invitation_delivery import (
    ConstructionInvitationDeliveryTokenError,
)
from services.construction.construction_invitation_delivery_material import (
    ConstructionInvitationDeliveryMaterial,
)
from services.construction.construction_invitation_email_handler import (
    ConstructionInvitationEmailHandler,
    build_construction_invitation_email_worker,
)
from services.construction.construction_jobs import (
    ConstructionBackgroundJobWorker,
    ConstructionJobPermanentError,
    ConstructionJobRetryError,
)


@pytest.fixture
def email_case():
    state = SimpleNamespace(
        now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
        events=[],
    )
    state.job = SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), resource_id=uuid4(),
        job_type="INVITATION_EMAIL",
        resource_type="construction_invitation",
    )
    state.material = ConstructionInvitationDeliveryMaterial(
        invitation_id=state.job.resource_id,
        tenant_id=state.job.tenant_id, email="person@example.com",
        expires_at=state.now + timedelta(days=7),
        token="private-email-handler-token",
    )
    state.session = SimpleNamespace(
        in_transaction=Mock(return_value=True),
        commit=AsyncMock(side_effect=AssertionError("Unexpected commit")),
        rollback=AsyncMock(side_effect=AssertionError("Unexpected rollback")),
    )

    async def load(**kwargs):
        state.events.append("load")
        return state.material

    async def send(**kwargs):
        assert state.session.in_transaction()
        state.events.append("send")
        return "private-provider-response"

    async def claim(**kwargs):
        state.events.append("claim")
        return state.job

    async def ready(**kwargs):
        assert state.session.in_transaction()
        state.events.append("ready")
        return kwargs["job_id"]

    state.loader = SimpleNamespace(
        session=state.session, load=AsyncMock(side_effect=load),
    )
    state.send = AsyncMock(side_effect=send)
    state.queue = SimpleNamespace(
        claim_next=AsyncMock(side_effect=claim),
        mark_ready=AsyncMock(side_effect=ready),
        mark_failed=AsyncMock(return_value=state.job.id),
        schedule_retry=AsyncMock(return_value=state.job.id),
    )
    state.handler = ConstructionInvitationEmailHandler(
        session=state.session, loader=state.loader,
        send_invitation=state.send,
    )
    state.worker = ConstructionBackgroundJobWorker(
        queue=state.queue, handlers={"INVITATION_EMAIL": state.handler},
        lease_owner="mail-worker", lease_duration=timedelta(minutes=5),
        clock=lambda: state.now,
    )
    return state


def no_transaction_close(state):
    state.session.commit.assert_not_awaited()
    state.session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_is_loaded_before_send_and_ready_without_private_result(email_case):
    state = email_case
    assert await state.worker.run_once() == state.job.id
    assert state.events == ["claim", "load", "send", "ready"]
    state.loader.load.assert_awaited_once_with(
        tenant_id=state.job.tenant_id, job_id=state.job.id,
    )
    state.send.assert_awaited_once_with(
        material=state.material, job_id=state.job.id,
    )
    result = state.queue.mark_ready.await_args.kwargs
    assert result["result_reference"] == (
        f"construction_invitation:{state.job.resource_id}"
    )
    assert state.material.token not in repr(result)
    assert "private-provider-response" not in repr(result)
    state.queue.mark_failed.assert_not_awaited()
    state.queue.schedule_retry.assert_not_awaited()
    no_transaction_close(state)


@pytest.mark.asyncio
async def test_unavailable_delivery_never_calls_sender(email_case):
    state = email_case
    state.loader.load.side_effect = ConstructionInvitationDeliveryTokenError(
        "private-token-and-ciphertext",
    )
    assert await state.worker.run_once() == state.job.id
    state.send.assert_not_awaited()
    state.queue.mark_ready.assert_not_awaited()
    failure = state.queue.mark_failed.await_args.kwargs
    assert failure["error_code"] == "INVITATION_DELIVERY_UNAVAILABLE"
    assert "private-token-and-ciphertext" not in repr(failure)
    no_transaction_close(state)


@pytest.mark.asyncio
async def test_lost_lease_does_not_attempt_any_queue_outcome(email_case):
    state = email_case
    error = JobLeaseLostError("Job lease is unavailable.")
    state.loader.load.side_effect = error
    with pytest.raises(JobLeaseLostError) as caught:
        await state.worker.run_once()
    assert caught.value is error
    state.send.assert_not_awaited()
    state.queue.mark_ready.assert_not_awaited()
    state.queue.mark_failed.assert_not_awaited()
    state.queue.schedule_retry.assert_not_awaited()
    no_transaction_close(state)


@pytest.mark.asyncio
async def test_explicit_transport_retry_uses_existing_queue_contract(email_case):
    state = email_case
    retry_at = state.now + timedelta(minutes=2)
    state.send.side_effect = ConstructionJobRetryError(
        error_code="EMAIL_PROVIDER_UNAVAILABLE", available_at=retry_at,
    )
    assert await state.worker.run_once() == state.job.id
    state.queue.schedule_retry.assert_awaited_once_with(
        job_id=state.job.id, lease_owner="mail-worker",
        error_code="EMAIL_PROVIDER_UNAVAILABLE",
        available_at=retry_at, now=state.now,
    )
    state.queue.mark_ready.assert_not_awaited()
    state.queue.mark_failed.assert_not_awaited()
    no_transaction_close(state)


@pytest.mark.asyncio
async def test_explicit_transport_failure_uses_existing_queue_contract(email_case):
    state = email_case
    state.send.side_effect = ConstructionJobPermanentError(
        error_code="EMAIL_DELIVERY_FAILED",
    )
    assert await state.worker.run_once() == state.job.id
    assert state.queue.mark_failed.await_args.kwargs["error_code"] == (
        "EMAIL_DELIVERY_FAILED"
    )
    state.queue.mark_ready.assert_not_awaited()
    state.queue.schedule_retry.assert_not_awaited()
    no_transaction_close(state)


@pytest.mark.asyncio
async def test_unexpected_provider_failure_does_not_store_private_details(email_case):
    state = email_case
    state.send.side_effect = RuntimeError(state.material.token)
    assert await state.worker.run_once() == state.job.id
    failure = state.queue.mark_failed.await_args.kwargs
    assert failure["error_code"] == "UNEXPECTED_JOB_ERROR"
    assert state.material.token not in repr(failure)
    state.queue.mark_ready.assert_not_awaited()
    no_transaction_close(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "invitation_id"])
async def test_loaded_material_cannot_cross_job_scope(email_case, field):
    state = email_case
    state.material = replace(state.material, **{field: uuid4()})
    assert await state.worker.run_once() == state.job.id
    state.send.assert_not_awaited()
    assert state.queue.mark_failed.await_args.kwargs["error_code"] == (
        "INVITATION_DELIVERY_CONTEXT_INVALID"
    )


@pytest.mark.asyncio
async def test_transaction_closed_during_loading_prevents_send(email_case):
    state = email_case

    async def closed_transaction(**kwargs):
        state.session.in_transaction.return_value = False
        return state.material

    state.loader.load.side_effect = closed_transaction
    with pytest.raises(ConstructionJobPermanentError) as caught:
        await state.handler(state.job)
    assert caught.value.error_code == "INVITATION_DELIVERY_CONTEXT_INVALID"
    state.send.assert_not_awaited()
    no_transaction_close(state)


@pytest.mark.asyncio
async def test_empty_queue_does_not_call_loader_or_sender(email_case):
    state = email_case
    state.queue.claim_next.side_effect = None
    state.queue.claim_next.return_value = None
    assert await state.worker.run_once() is None
    state.loader.load.assert_not_awaited()
    state.send.assert_not_awaited()
    state.queue.mark_ready.assert_not_awaited()


def test_worker_factory_preserves_handlers_and_uses_one_session(email_case):
    state = email_case
    existing_handler = AsyncMock()
    existing = {"EXPORT_PDF": existing_handler}
    worker = build_construction_invitation_email_worker(
        session=state.session,
        encryption_key=Fernet.generate_key().decode("ascii"),
        send_invitation=state.send, lease_owner="mail-worker",
        lease_duration=timedelta(minutes=5),
        clock=lambda: state.now, handlers=existing,
    )
    handler = worker.handlers["INVITATION_EMAIL"]
    assert worker.queue.session is state.session
    assert handler.session is state.session
    assert handler.loader.session is state.session
    assert handler.loader.invitation_repository.session is state.session
    assert handler.loader.lease_owner == worker.lease_owner
    assert worker.handlers["EXPORT_PDF"] is existing_handler
    assert existing == {"EXPORT_PDF": existing_handler}


def test_factory_does_not_overwrite_an_existing_invitation_handler(email_case):
    state = email_case
    with pytest.raises(ValueError, match="already configured"):
        build_construction_invitation_email_worker(
            session=state.session,
            encryption_key=Fernet.generate_key().decode("ascii"),
            send_invitation=state.send, lease_owner="mail-worker",
            lease_duration=timedelta(minutes=5),
            clock=lambda: state.now,
            handlers={"INVITATION_EMAIL": AsyncMock()},
        )
