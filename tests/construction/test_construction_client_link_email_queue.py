"""Real local rollback/replay of request, audit, receipt and email job.

SQLite checks transaction composition. PostgreSQL RLS, durable worker delivery
and deployed authentication are outside these local tests.
"""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction.construction_client_link_email_queue import (
    ConstructionClientLinkEmailQueue,
    JOB_TYPE, RESOURCE_TYPE, email_job_key, parse_email_job_key,
)
from services.construction.construction_client_link_issuance_workflow import (
    ConstructionClientLinkIssuanceWorkflow,
)
from services.construction.construction_clients import ConstructionClientOperationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError

from test_construction_client_link_exact_email import email_pipeline, issue, EMAIL
from test_construction_1a_client_link_issuance_workflow import (
    issuance_pipeline, snapshot,
)
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import (
    sql_confirmation, seed_request,
)
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db

@pytest.fixture
async def queued(email_pipeline):
    p, c = email_pipeline, email_pipeline.c
    c.sync.execute(text(
        "CREATE TABLE client_link_test_email_jobs "
        "(id TEXT PRIMARY KEY, data TEXT NOT NULL)"
    ))
    c.sync.commit()
    p.raw_jobs = []
    fields = (
        "id", "tenant_id", "user_id", "resource_type", "resource_id",
        "job_type", "status", "idempotency_key", "attempts",
        "available_at", "progress", "lease_owner", "lease_expires_at",
        "error_code", "result_reference", "created_at", "updated_at",
    )
    async def enqueue(*, job):
        values = {
            field: str(value) if isinstance(value, UUID)
            else value.isoformat() if hasattr(value, "isoformat")
            else value
            for field in fields
            for value in (getattr(job, field),)
        }
        p.raw_jobs.append(deepcopy(values))
        c.sync.execute(
            text("INSERT INTO client_link_test_email_jobs (id, data) VALUES (:id, :data)"),
            {"id": str(job.id), "data": json.dumps(values, sort_keys=True)},
        )
        return job.id
    p.queue = NS(session=c.session, enqueue=AsyncMock(side_effect=enqueue))
    p.delivery = ConstructionClientLinkEmailQueue(
        session=c.session, queue=p.queue,
        token_deriver=p.deriver, now_provider=lambda: c.now,
    )
    p.workflow = ConstructionClientLinkIssuanceWorkflow(
        session=c.session, issuance_service=p.issue_service,
        idempotency_service=p.idempotency, token_deriver=p.deriver,
        now_provider=lambda: c.now, target_email_reader=p.email_reader,
        email_delivery_queue=p.delivery,
    )
    return p

def jobs(p):
    return p.c.sync.execute(
        text("SELECT id, data FROM client_link_test_email_jobs ORDER BY id")
    ).all()

def all_state(p):
    return snapshot(p), jobs(p)

@pytest.mark.asyncio
async def test_one_commit_contains_job_request_audit_and_encrypted_receipt(queued):
    p, c = queued, queued.c
    result = await issue(p)
    p.queue.enqueue.assert_awaited_once()
    assert len(jobs(p)) == 1 and len(c.audit_rows) == 1
    job = p.raw_jobs[0]
    record = p.ledger.all()[0]
    receipt = p.codec.decrypt(record.response_ciphertext)
    delivery = receipt["email_delivery"]
    assert job["job_type"] == JOB_TYPE
    assert job["resource_type"] == RESOURCE_TYPE
    assert job["resource_id"] == str(result.request.request_id)
    assert job["tenant_id"] == str(c.tenant)
    assert job["user_id"] == str(c.operator)
    assert parse_email_job_key(job["idempotency_key"]) == (
        record.id, result.request.request_id,
    )
    assert delivery["job_id"] == job["id"]
    assert delivery["command"]["target_email"] == EMAIL
    assert delivery["command"]["expected_row_version"] == 1
    assert "target_user_id" not in delivery["command"]
    assert result.request.expires_at - result.request.issued_at == timedelta(hours=24)
    c.session.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_token_not_stored_even_inside_encrypted_receipt(queued):
    p = queued
    result = await issue(p)
    record = p.ledger.all()[0]
    receipt = p.codec.decrypt(record.response_ciphertext)
    serialized = json.dumps(receipt, sort_keys=True)
    assert result.token not in serialized
    assert result.request.token_hash not in serialized
    assert EMAIL not in jobs(p)[0][1]
    assert result.token not in jobs(p)[0][1]
    assert result.request.token_hash not in jobs(p)[0][1]

    # Delivery can recreate the same token from the protected command and
    # immutable reservation context, without persisting the token itself.
    recreated = p.deriver.derive(
        reservation_id=record.id, reserved_at=record.created_at,
        tenant_id=record.tenant_id, initiated_by_user_id=record.principal_id,
        payload=receipt["email_delivery"]["command"],
    )
    assert recreated == result.token

@pytest.mark.asyncio
async def test_replay_does_not_enqueue_or_change_target_or_expiry(queued):
    p = queued
    first = await issue(p)
    before = all_state(p)
    p.email_reader.find_verified_user_id.return_value = p.c.foreign_user
    p.c.now += timedelta(hours=1)
    second = await issue(p, target_email=" PERSON@EXAMPLE.COM ")
    assert second == first and all_state(p) == before
    p.queue.enqueue.assert_awaited_once()
    p.email_reader.find_verified_user_id.assert_awaited_once()

@pytest.mark.asyncio
async def test_changed_email_with_same_key_cannot_enqueue_another_job(queued):
    p = queued
    await issue(p)
    before = all_state(p)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await issue(p, target_email="other@example.com")
    assert all_state(p) == before
    p.queue.enqueue.assert_awaited_once()

@pytest.mark.asyncio
async def test_authorization_denial_prevents_enqueue(queued):
    p = queued
    p.c.access.has_active_platform_membership.return_value = False
    with pytest.raises(ConstructionPermissionDeniedError):
        await issue(p)
    p.queue.enqueue.assert_not_awaited()
    assert not jobs(p)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "queue", "invalid_job_id", "audit", "completion", "commit", "cancellation",
])
async def test_failure_rolls_back_job_request_audit_and_receipt_together(queued, failure):
    p = queued
    before = all_state(p)
    original = p.queue.enqueue.side_effect
    if failure in ("queue", "invalid_job_id", "cancellation"):
        async def failing(*, job):
            await original(job=job)
            if failure == "invalid_job_id":
                return None
            if failure == "cancellation":
                raise asyncio.CancelledError()
            raise RuntimeError("private queue failure")
        p.queue.enqueue.side_effect = failing
    elif failure == "audit":
        p.c.events.create_event.side_effect = RuntimeError("private audit failure")
    elif failure == "completion":
        p.flags.fail_complete = True
    else:
        p.flags.fail_commit = True

    error = asyncio.CancelledError if failure == "cancellation" else ConstructionClientOperationError
    with pytest.raises(error):
        await issue(p)
    assert all_state(p) == before
    p.c.session.rollback.assert_awaited()

@pytest.mark.asyncio
async def test_new_idempotency_window_gets_distinct_job_for_new_request(queued):
    p = queued
    first = await issue(p)
    first_record_id = p.ledger.all()[0].id
    p.c.now += timedelta(hours=25)
    second = await issue(p)
    assert second.request.request_id != first.request.request_id
    assert second.token != first.token
    assert p.ledger.all()[0].id == first_record_id
    assert len(jobs(p)) == 2
    assert p.raw_jobs[0]["idempotency_key"] != p.raw_jobs[1]["idempotency_key"]

@pytest.mark.asyncio
async def test_queue_failure_during_reissuance_preserves_previous_active_request(queued):
    p = queued
    await issue(p)
    before = all_state(p)
    p.queue.enqueue.side_effect = RuntimeError("queue unavailable")
    with pytest.raises(ConstructionClientOperationError):
        await issue(p, idempotency_key="new-issuance-key")
    assert all_state(p) == before

@pytest.mark.asyncio
async def test_existing_receipt_without_delivery_is_not_backfilled_on_replay(queued):
    p = queued
    plain = ConstructionClientLinkIssuanceWorkflow(
        session=p.c.session, issuance_service=p.issue_service,
        idempotency_service=p.idempotency, token_deriver=p.deriver,
        now_provider=lambda: p.c.now, target_email_reader=p.email_reader,
    )
    configured = p.workflow
    p.workflow = plain
    await issue(p)
    before = all_state(p)
    p.workflow = configured
    with pytest.raises(ConstructionClientOperationError):
        await issue(p)
    assert all_state(p) == before
    p.queue.enqueue.assert_not_awaited()

def test_job_key_is_canonical_and_contains_only_context_identifiers():
    reservation_id, request_id = uuid4(), uuid4()
    key = email_job_key(reservation_id=reservation_id, request_id=request_id)
    assert parse_email_job_key(key) == (reservation_id, request_id)
    for invalid in (
        "", "client-link-email:bad:bad",
        key + ":extra", key.replace("client-link-email", "invitation"),
    ):
        with pytest.raises(ConstructionClientOperationError):
            parse_email_job_key(invalid)

# client_link_atomic_email_queue_v1
