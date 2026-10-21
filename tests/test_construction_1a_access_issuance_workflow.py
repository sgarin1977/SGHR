from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction_access_issuance import ConstructionAccessIssuanceService
from services.construction_access_issuance_workflow import (
    ConstructionAccessIssuanceWorkflow, build_construction_access_issuance_workflow,
)
from services.construction_access_workflows import ConstructionAccessIdempotencyInProgressError, PUBLIC_GRANT_FIELDS
from services.construction_grants import ConstructionGrantOperationError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError
from test_construction_1a_access_mutation_workflow import pipeline, snapshot


@pytest.fixture
def issuance_pipeline(pipeline):
    c = pipeline
    c.issuance = ConstructionAccessIssuanceService(
        session=c.session, repository=c.repo, project_repository=c.service.project_repository,
        event_repository=c.service.audit_logger.event_repository, now_provider=lambda: c.now,
    )
    c.issuance_flow = ConstructionAccessIssuanceWorkflow(
        session=c.session, issuance_service=c.issuance, idempotency_service=c.idem,
    )
    return c


async def issue(c, **changes):
    if not c.db.in_transaction:
        c.db.execute("BEGIN")
    values = dict(
        actor=c.actor, target_user_id=c.original.user_id, role="SURVEYOR",
        scope_type="project", scope_id=c.project_id, expires_at=None,
        idempotency_key=None, trace_id="issuance-workflow",
    )
    values.update(changes)
    return await c.issuance_flow.grant_access(**values)


def overlap():
    driver = RuntimeError("private overlap diagnostic")
    driver.sqlstate = "23P01"
    driver.constraint_name = "ex_construction_access_grants_active_period"
    return IntegrityError("private SQL", {}, driver)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "create-key"])
async def test_issuance_commits_grant_audit_and_optional_encrypted_response_once(issuance_pipeline, key):
    c = issuance_pipeline
    result = await issue(c, idempotency_key=key)
    assert set(result) == PUBLIC_GRANT_FIELDS and result["status"] == "active"
    assert len(c.rows("grant")) == 2 and len(c.rows("audit")) == 1
    assert c.rows("audit")[0].event_type == "construction_access_grant_created"
    assert c.rows("audit")[0].trace_id == "issuance-workflow"
    c.session.commit.assert_awaited_once()
    records = c.rows("ledger")
    assert len(records) == (0 if key is None else 1)
    if key is not None:
        record = records[0]
        assert record.operation == "construction.access_grant.create" and record.response_status == 201
        assert record.tenant_id == c.actor.tenant_id and record.principal_id == c.actor.user_id
        assert record.status == "completed" and c.codec.decrypt(record.response_ciphertext) == result
        assert result["id"].encode() not in record.response_ciphertext


@pytest.mark.asyncio
async def test_creation_replay_does_not_issue_or_audit_again(issuance_pipeline):
    c = issuance_pipeline
    first = await issue(c, idempotency_key="key")
    before = snapshot(c)
    assert await issue(c, idempotency_key="key") == first and snapshot(c) == before
    assert c.repo.create_grant.await_count == 1 and len(c.rows("audit")) == 1
    assert c.session.commit.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["expired", "revoked"])
async def test_replay_of_historical_result_never_reactivates_grant(issuance_pipeline, state):
    c = issuance_pipeline
    deadline = c.now+timedelta(minutes=1)
    first = await issue(c, idempotency_key="key", expires_at=deadline)
    if state == "expired":
        c.now += timedelta(minutes=2)
    else:
        record = next(g for g in c.rows("grant") if str(g.id) == first["id"])
        record.status, record.revoked_at, record.revoked_by_user_id = "revoked", c.now, c.actor.user_id
        c.save("grant", record.id, record)
        c.db.commit()
    before = snapshot(c)
    assert await issue(c, idempotency_key="key", expires_at=deadline) == first
    assert snapshot(c) == before and c.repo.create_grant.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_issuance_workflow_failure_rolls_back_everything(issuance_pipeline, failure):
    c = issuance_pipeline
    before = snapshot(c)
    if failure == "audit":
        original = c.service.audit_logger.event_repository.create_event.side_effect
        async def audit(**fields):
            await original(**fields)
            raise RuntimeError("private audit diagnostic")
        c.service.audit_logger.event_repository.create_event.side_effect = audit
    else:
        setattr(c, "fail_complete" if failure == "complete" else "fail_commit", True)
    with pytest.raises(ConstructionGrantOperationError) as caught:
        await issue(c, idempotency_key="key")
    assert "private" not in str(caught.value) and snapshot(c) == before
    assert not c.db.in_transaction
    c.session.rollback.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"role": "EXECUTOR"}, {"target_user_id": uuid4()},
    {"scope_id": uuid4()}, {"expires_at": None},
])
async def test_same_key_different_issuance_payload_is_rejected(issuance_pipeline, changes):
    c = issuance_pipeline
    deadline = c.now+timedelta(hours=1)
    await issue(c, idempotency_key="key", expires_at=deadline)
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await issue(c, idempotency_key="key", **{**{"expires_at": deadline}, **changes})
    assert snapshot(c) == before and c.repo.create_grant.await_count == 1


@pytest.mark.asyncio
async def test_replay_rechecks_current_authority(issuance_pipeline):
    c = issuance_pipeline
    await issue(c, idempotency_key="key")
    before = snapshot(c)
    c.authorized = False
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await issue(c, idempotency_key="key")
    assert snapshot(c) == before and c.session.commit.await_count == 1


@pytest.mark.asyncio
async def test_fresh_issuance_rechecks_rights_after_reservation_wait(issuance_pipeline):
    c = issuance_pipeline
    before = snapshot(c)
    original = c.idem.reserve
    async def reserve(**fields):
        value = await original(**fields)
        c.authorized = False
        return value
    c.idem.reserve = reserve
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await issue(c, idempotency_key="key")
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_called()


@pytest.mark.asyncio
async def test_replay_rechecks_rights_after_grant_lock_wait(issuance_pipeline):
    c = issuance_pipeline
    await issue(c, idempotency_key="key")
    before = snapshot(c)
    original = c.repo.get_grant_for_update.side_effect
    async def lock(**fields):
        grant = await original(**fields)
        c.authorized = False
        return grant
    c.repo.get_grant_for_update.side_effect = lock
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await issue(c, idempotency_key="key")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("id", "invalid"), ("user_id", str(uuid4())), ("scope_id", str(uuid4())),
    ("status", "revoked"), ("secret", "private-response-secret"),
    ("granted_at", "2026-10-06T12:00:00"),
])
async def test_invalid_cached_response_is_never_returned(issuance_pipeline, field, value):
    c = issuance_pipeline
    await issue(c, idempotency_key="key")
    record = c.rows("ledger")[0]
    response = c.codec.decrypt(record.response_ciphertext)
    response[field] = value
    record.response_ciphertext = c.codec.encrypt(response)
    c.save("ledger", record.id, record)
    c.db.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionGrantOperationError):
        await issue(c, idempotency_key="key")
    assert snapshot(c) == before and c.session.commit.await_count == 1


@pytest.mark.asyncio
async def test_processing_reservation_does_not_issue_grant(issuance_pipeline):
    c = issuance_pipeline
    c.db.execute("BEGIN")
    await c.idem.reserve(
        tenant_id=c.actor.tenant_id, principal_type="user", principal_id=c.actor.user_id,
        operation="construction.access_grant.create", idempotency_key="processing",
        payload={"target_user_id": str(c.original.user_id), "role": "SURVEYOR", "scope_type": "project",
                 "scope_id": str(c.project_id), "expires_at": None}, now=c.now,
    )
    c.db.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionAccessIdempotencyInProgressError):
        await issue(c, idempotency_key="processing")
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["insert", "commit"])
async def test_effective_duplicate_conflict_rolls_back_reservation(issuance_pipeline, stage):
    c = issuance_pipeline
    before = snapshot(c)
    if stage == "insert":
        c.repo.create_grant.side_effect = overlap()
    else:
        c.session.commit.side_effect = overlap()
    with pytest.raises(ConstructionAccessGrantConflictError) as caught:
        await issue(c, idempotency_key="key")
    assert caught.value.code == "ACCESS_GRANT_CONFLICT" and snapshot(c) == before


@pytest.mark.asyncio
async def test_optional_idempotency_and_canonical_request_normalization(issuance_pipeline):
    c = issuance_pipeline
    deadline = c.now+timedelta(hours=1)
    first = await issue(c, idempotency_key="key", expires_at=deadline)
    before = snapshot(c)
    second = await issue(
        c, idempotency_key="key", role=" surveyor ", scope_type=" PROJECT ",
        expires_at=deadline.astimezone(timezone(timedelta(hours=3))),
    )
    assert second == first and snapshot(c) == before
    c.issuance_flow.idempotency = None
    with pytest.raises(ConstructionGrantOperationError):
        await issue(c, idempotency_key="other")
    assert snapshot(c) == before


def test_issuance_workflow_factory_shares_session_and_optional_crypto(issuance_pipeline):
    c = issuance_pipeline
    plain = build_construction_access_issuance_workflow(session=c.session, now_provider=lambda: c.now)
    assert plain.idempotency is None and plain.service.repository.session is c.session
    protected = build_construction_access_issuance_workflow(
        session=c.session, idempotency_encryption_key=Fernet.generate_key().decode(), now_provider=lambda: c.now,
    )
    assert protected.idempotency.repository.session is c.session
    assert protected.service.audit_logger.event_repository.session is c.session
    with pytest.raises(ValueError):
        ConstructionAccessIssuanceWorkflow(
            session=c.session, issuance_service=c.issuance,
            idempotency_service=SimpleNamespace(repository=SimpleNamespace(session=object())),
        )


@pytest.mark.asyncio
async def test_replay_requires_the_original_persisted_grant_binding(issuance_pipeline):
    c = issuance_pipeline
    first = await issue(c, idempotency_key="key")
    record = next(g for g in c.rows("grant") if str(g.id) == first["id"])
    record.user_id = uuid4()
    c.save("grant", record.id, record)
    c.db.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionGrantOperationError):
        await issue(c, idempotency_key="key")
    assert snapshot(c) == before and c.repo.create_grant.await_count == 1


@pytest.mark.asyncio
async def test_replay_does_not_combine_authority_from_unrelated_projects(issuance_pipeline):
    c = issuance_pipeline
    await issue(c, idempotency_key="key", role="OWNER")
    before = snapshot(c)
    c.repo.list_active_grants.side_effect = None
    c.repo.list_active_grants.return_value = [
        SimpleNamespace(role="ADMIN", scope_type="project", scope_id=c.project_id),
        SimpleNamespace(role="OWNER", scope_type="project", scope_id=uuid4()),
    ]
    with pytest.raises(ConstructionPermissionDeniedError):
        await issue(c, idempotency_key="key", role="OWNER")
    assert snapshot(c) == before and c.repo.create_grant.await_count == 1


@pytest.mark.asyncio
async def test_same_key_is_scoped_to_caller_and_expired_reservations_do_not_replay(issuance_pipeline):
    c = issuance_pipeline
    await issue(c, idempotency_key="key")
    before = snapshot(c)
    c.repo.create_grant.side_effect = overlap()
    c.actor.user_id = uuid4()
    with pytest.raises(ConstructionAccessGrantConflictError):
        await issue(c, idempotency_key="key")
    assert snapshot(c) == before and len(c.rows("ledger")) == 1
    c.actor.user_id = c.rows("ledger")[0].principal_id
    c.now += timedelta(hours=25)
    with pytest.raises(ConstructionAccessGrantConflictError):
        await issue(c, idempotency_key="key")
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "   ", 123, "x"*256])
async def test_invalid_optional_key_never_reserves_or_creates(issuance_pipeline, key):
    c = issuance_pipeline
    before = snapshot(c)
    with pytest.raises(ValueError):
        await issue(c, idempotency_key=key)
    assert snapshot(c) == before and "reserve" not in c.calls
    c.repo.create_grant.assert_not_called()
