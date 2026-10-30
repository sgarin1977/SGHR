"""Local delivery validation and transaction tests; no real email or Telegram."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from api.settings import ApiConfigurationError
from database.repositories.job_queue import JobLeaseLostError
from services.construction.construction_client_link_email_worker import (
    ClientLinkEmailLoader, ClientLinkEmailSender, ClientLinkEmailHandler,
    JOB_TYPE, RESOURCE_TYPE, MESSAGES, LANGUAGES,
)
from services.construction.construction_client_link_email_runtime import (
    ClientLinkEmailQueue, ClientLinkEmailSettings,
    process_client_link_email_once,
)
from services.construction.construction_client_link_recipient_context import (
    ConstructionClientLinkRecipientContextCodec,
)
from services.construction.construction_jobs import (
    ConstructionBackgroundJobWorker,
    ConstructionJobPermanentError, ConstructionJobRetryError,
)

from test_construction_client_link_email_queue import queued
from test_construction_client_link_exact_email import email_pipeline, issue
from test_construction_1a_client_link_issuance_workflow import (
    issuance_pipeline, snapshot,
)
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation, seed_request
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db
from test_construction_invitation_worker_runtime import queue_db

@pytest.fixture
async def delivery(queued):
    p, c = queued, queued.c
    p.material = await issue(p)
    p.record = deepcopy(p.ledger.all()[0])
    values = deepcopy(p.raw_jobs[0])
    for name in ("id", "tenant_id", "user_id", "resource_id"):
        values[name] = UUID(values[name])
    for name in ("available_at", "created_at", "updated_at"):
        values[name] = datetime.fromisoformat(values[name])
    values.update(
        status="RUNNING", attempts=1, lease_owner="email-test",
        lease_expires_at=c.now + timedelta(minutes=2),
    )
    p.job = NS(**values)
    p.worker_records = NS(
        session=c.session,
        find_completed=AsyncMock(side_effect=lambda **kwargs: p.record),
    )
    p.worker_principals = NS(
        session=c.session,
        get_active_user_id=AsyncMock(side_effect=lambda **kwargs: kwargs["user_id"]),
    )
    p.worker_emails = NS(
        session=c.session,
        find_verified_user_id=AsyncMock(return_value=c.target),
    )
    p.loader = ClientLinkEmailLoader(
        session=c.session, records=p.worker_records,
        clients=c.clients, requests=c.requests, access=c.access,
        principals=p.worker_principals, emails=p.worker_emails,
        receipt_validator=p.delivery, response_codec=p.codec,
        token_deriver=p.deriver, lease_owner="email-test", clock=lambda: c.now,
    )
    p.provider = NS(send_email=AsyncMock())
    p.sender = ClientLinkEmailSender(
        provider=p.provider, from_address="noreply@example.com",
        confirmation_page_url="https://app.example.com/client-link/confirm",
        encryption_key=p.key, clock=lambda: c.now,
    )
    p.handler = ClientLinkEmailHandler(
        session=c.session, loader=p.loader, sender=p.sender,
    )
    c.session.commit.reset_mock()
    c.session.rollback.reset_mock()
    if not c.sync.in_transaction():
        c.sync.begin()
    return p

@pytest.mark.asyncio
async def test_loaded_email_uses_original_token_and_generic_protected_link(delivery):
    p, c = delivery, delivery.c
    before = snapshot(p)
    result = await p.handler(p.job)
    assert result == RESOURCE_TYPE + ":" + str(p.material.request.request_id)
    p.provider.send_email.assert_awaited_once()
    sent = p.provider.send_email.await_args.kwargs
    assert sent["to_address"] == "person@example.com"
    link = next(
        line for line in sent["text_body"].splitlines()
        if line.startswith("https://")
    )
    parsed = urlsplit(link)
    assert parsed.query == ""
    proof = parse_qs(parsed.fragment)
    assert set(proof) == {"recipient_context", "token"}
    assert proof["token"] == [p.material.token]
    context = ConstructionClientLinkRecipientContextCodec(
        encryption_key=p.key,
    ).resolve(
        recipient_context=proof["recipient_context"][0],
        token=proof["token"][0],
    )
    assert context.target_user_id == c.target
    assert context.tenant_id == c.tenant
    assert context.request_id == p.material.request.request_id
    assert snapshot(p) == before
    c.session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_material_repr_does_not_expose_email_or_token(delivery):
    material = await delivery.loader.load(delivery.job)
    assert delivery.material.token not in repr(material)
    assert material.email not in repr(material)

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "expired", "consumed", "invalidated", "client_version", "previous_link",
    "archived", "membership", "revoked", "initiator", "target",
    "email_changed_owner", "corrupt_receipt", "wrong_request", "renewed_record",
])
async def test_unavailable_delivery_never_calls_email_provider(delivery, failure):
    p, c = delivery, delivery.c
    if failure == "expired":
        c.now += timedelta(hours=24)
        p.job.lease_expires_at = c.now + timedelta(minutes=2)
    elif failure in ("consumed", "invalidated"):
        c.now += timedelta(seconds=1)
        column = "consumed_at" if failure == "consumed" else "invalidated_at"
        c.sync.execute(text(
            "UPDATE construction_client_user_link_requests "
            "SET " + column + "=:now, updated_at=:now, row_version=2 WHERE id=:id"
        ), {"now": c.now, "id": p.material.request.request_id.hex})
    elif failure in ("client_version", "previous_link", "archived"):
        if failure == "client_version":
            clause, values = "row_version=2", {}
        elif failure == "previous_link":
            clause, values = "linked_user_id=:value", {"value": c.foreign_user.hex}
        else:
            clause, values = "deleted_at=:value", {"value": c.now}
        c.sync.execute(text(
            "UPDATE construction_clients SET " + clause + " WHERE id=:id"
        ), {"id": c.client.hex, **values})
    elif failure == "membership":
        c.access.has_active_platform_membership.return_value = False
    elif failure == "revoked":
        c.grant.status = "revoked"
    elif failure in ("initiator", "target"):
        blocked = c.operator if failure == "initiator" else c.target
        p.worker_principals.get_active_user_id.side_effect = (
            lambda **kwargs: None if kwargs["user_id"] == blocked else kwargs["user_id"]
        )
    elif failure == "email_changed_owner":
        p.worker_emails.find_verified_user_id.return_value = c.foreign_user
    elif failure == "corrupt_receipt":
        p.record.response_ciphertext = b"private-invalid-ciphertext"
    elif failure == "wrong_request":
        p.job.resource_id = uuid4()
    else:
        p.record.created_at += timedelta(days=2)
    with pytest.raises(ConstructionJobPermanentError):
        await p.handler(p.job)
    p.provider.send_email.assert_not_awaited()

@pytest.mark.asyncio
async def test_lost_lease_propagates_for_transaction_rollback(delivery):
    p = delivery
    p.job.lease_owner = "another-worker"
    with pytest.raises(JobLeaseLostError):
        await p.handler(p.job)
    p.provider.send_email.assert_not_awaited()

@pytest.mark.asyncio
async def test_provider_retry_keeps_original_request_and_token(delivery):
    p = delivery
    loaded = await p.loader.load(p.job)
    p.provider.send_email.side_effect = RuntimeError("private provider body")
    with pytest.raises(ConstructionJobRetryError) as caught:
        await p.sender.send(material=loaded, job=p.job)
    assert caught.value.available_at == p.c.now + timedelta(seconds=60)
    assert "private provider" not in str(caught.value)
    first = p.provider.send_email.await_args.kwargs["text_body"]
    p.job.attempts = 2
    p.provider.send_email.side_effect = None
    await p.sender.send(material=loaded, job=p.job)
    second = p.provider.send_email.await_args.kwargs["text_body"]
    assert first == second and p.material.token in first

@pytest.mark.asyncio
async def test_provider_failure_at_final_attempt_is_terminal(delivery):
    p = delivery
    loaded = await p.loader.load(p.job)
    p.job.attempts = 3
    p.provider.send_email.side_effect = RuntimeError("private provider body")
    with pytest.raises(ConstructionJobPermanentError) as caught:
        await p.sender.send(material=loaded, job=p.job)
    assert caught.value.error_code == "CLIENT_LINK_EMAIL_FAILED"
    assert "private provider" not in str(caught.value)

@pytest.fixture
def config_values():
    return {
        "EMAIL_PROVIDER": "resend", "EMAIL_FROM": "noreply@example.com",
        "WEB_APP_URL": "https://app.example.com",
        "RESEND_API_KEY": "re_private_test_value",
        "API_IDEMPOTENCY_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "CONSTRUCTION_CLIENT_LINK_CONFIRMATION_PAGE_URL":
            "https://app.example.com/client-link/confirm",
    }

def test_settings_are_explicit_and_secrets_are_redacted(config_values):
    settings = ClientLinkEmailSettings.from_env(config_values)
    assert settings.lease_seconds == 120 and settings.job_timeout_seconds == 30
    assert config_values["RESEND_API_KEY"] not in repr(settings)
    assert config_values["API_IDEMPOTENCY_ENCRYPTION_KEY"] not in repr(settings)
    assert set(MESSAGES) == set(LANGUAGES)

@pytest.mark.parametrize("name,value", [
    ("CONSTRUCTION_CLIENT_LINK_CONFIRMATION_PAGE_URL", ""),
    ("CONSTRUCTION_CLIENT_LINK_CONFIRMATION_PAGE_URL", "http://app.example.com/page"),
    ("CONSTRUCTION_CLIENT_LINK_CONFIRMATION_PAGE_URL", "https://foreign.example.com/page"),
    ("CONSTRUCTION_CLIENT_LINK_CONFIRMATION_PAGE_URL", "https://app.example.com/page?token=x"),
    ("CONSTRUCTION_CLIENT_LINK_WORKER_POLL_SECONDS", "nan"),
    ("CONSTRUCTION_CLIENT_LINK_WORKER_LEASE_SECONDS", "20"),
    ("CONSTRUCTION_CLIENT_LINK_EMAIL_LANGUAGE", "unknown"),
])
def test_invalid_config_has_no_runtime_side_effects(config_values, name, value):
    config_values[name] = value
    with pytest.raises((ValueError, ApiConfigurationError)):
        ClientLinkEmailSettings.from_env(config_values)

@pytest.mark.asyncio
async def test_queue_sql_claims_only_client_link_jobs_with_skip_locked():
    session = NS(
        in_transaction=lambda: True, no_autoflush=nullcontext(),
        execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: None)),
        flush=AsyncMock(),
    )
    now = datetime.fromisoformat("2026-10-29T12:00:00+00:00")
    assert await ClientLinkEmailQueue(session).claim_next(
        now=now, lease_owner="test", lease_expires_at=now + timedelta(minutes=2),
    ) is None
    compiled = session.execute.await_args.args[0].compile(
        dialect=postgresql.dialect(),
    )
    assert JOB_TYPE in compiled.params.values()
    assert "INVITATION_EMAIL" not in compiled.params.values()
    assert "FOR UPDATE SKIP LOCKED" in str(compiled)
    session.flush.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("fail_commit", [False, True])
async def test_real_local_claim_outcome_and_commit_failure_are_atomic(
    queue_db, config_values, fail_commit,
):
    c = queue_db
    settings = ClientLinkEmailSettings.from_env(config_values)
    # Seed the relevant job through the local SQLite fixture only.
    with c.engine.begin() as connection:
        connection.execute(text(
            "UPDATE construction_background_jobs SET job_type=:kind WHERE id=:id"
        ), {"kind": JOB_TYPE, "id": UUID(int=2).hex})
    before = c.states()
    c.commits, c.rollbacks, c.fail_commit = 0, 0, fail_commit
    delivered = AsyncMock(return_value="safe-result")
    def factory(*, session, settings, lease_owner):
        return ConstructionBackgroundJobWorker(
            queue=ClientLinkEmailQueue(session),
            handlers={JOB_TYPE: delivered}, lease_owner=lease_owner,
            lease_duration=timedelta(seconds=120), clock=lambda: c.now,
        )
    action = process_client_link_email_once(
        session_factory=c.factory, settings=settings, lease_owner="test-worker",
        worker_factory=factory, verify_connection=AsyncMock(),
    )
    if fail_commit:
        with pytest.raises(RuntimeError):
            await action
        assert c.states() == before and c.rollbacks == 1
    else:
        assert await action == UUID(int=2)
        assert c.states()[2][:2] == ("READY", 1)
        assert c.commits == 1
    delivered.assert_awaited_once()

# client_link_email_worker_v1
