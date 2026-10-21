import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet

from services.api_idempotency import (
    ApiIdempotencyKeyReusedError, ApiIdempotencyResponseCodec, ApiIdempotencyService,
)
from services.construction_access_errors import ConstructionAccessGrantConflictError
from services.construction_access_mutations import ConstructionAccessMutationService
from services.construction_access_workflows import (
    ConstructionAccessIdempotencyInProgressError, ConstructionAccessMutationWorkflow,
    PUBLIC_GRANT_FIELDS, build_construction_access_mutation_workflow,
)
from services.construction_grants import ConstructionAccessContextResolver, ConstructionGrantOperationError
from services.construction_permissions import ConstructionPermissionDeniedError, ConstructionScopeDeniedError


def encode(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def unpack(payload):
    data = json.loads(payload)
    for key, value in data.items():
        if value is not None and key in {
            "id", "tenant_id", "user_id", "scope_id", "granted_by_user_id", "revoked_by_user_id", "principal_id",
        }:
            data[key] = UUID(value)
        elif value is not None and key in {"expires_at", "granted_at", "revoked_at", "created_at", "completed_at"}:
            data[key] = datetime.fromisoformat(value)
        elif value is not None and key == "response_ciphertext":
            data[key] = bytes.fromhex(value)
    return SimpleNamespace(**data)


@pytest.fixture
def pipeline():
    c = SimpleNamespace(now=datetime(2026, 10, 6, 12, tzinfo=timezone.utc), calls=[], authorized=True,
                        fail_complete=False, fail_commit=False, fail_second_audit=False)
    c.db = sqlite3.connect(":memory:")
    c.db.execute("CREATE TABLE state (kind TEXT, key TEXT, payload TEXT, PRIMARY KEY(kind,key))")
    def save(kind, key, value):
        c.db.execute("INSERT OR REPLACE INTO state VALUES (?,?,?)",
                     (kind, str(key), json.dumps(vars(value), default=encode, sort_keys=True)))
    def rows(kind):
        return [unpack(row[0]) for row in c.db.execute("SELECT payload FROM state WHERE kind=? ORDER BY key", (kind,))]
    c.save, c.rows = save, rows
    c.actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4(), status="active")
    c.project_id = uuid4()
    c.original = SimpleNamespace(
        id=uuid4(), tenant_id=c.actor.tenant_id, user_id=uuid4(), role="ESTIMATOR",
        scope_type="project", scope_id=c.project_id, expires_at=None, status="active",
        granted_at=c.now-timedelta(days=1), granted_by_user_id=uuid4(),
        revoked_at=None, revoked_by_user_id=None, internal_secret="never-public",
    )
    save("grant", c.original.id, c.original)
    c.db.commit()
    async def commit():
        c.calls.append("commit")
        if c.fail_commit:
            raise RuntimeError("private commit diagnostic")
        c.db.commit()
    c.session = SimpleNamespace(
        in_transaction=lambda: c.db.in_transaction,
        commit=AsyncMock(side_effect=commit), rollback=AsyncMock(side_effect=c.db.rollback),
    )
    async def lock(**fields):
        c.calls.append("lock")
        return next((g for g in rows("grant") if g.id == fields["grant_id"] and g.tenant_id == fields["tenant_id"]), None)
    async def active(**fields):
        c.calls.append("authorize")
        assert fields["tenant_id"] == c.actor.tenant_id and fields["user_id"] == c.actor.user_id
        return [SimpleNamespace(role="OWNER", scope_type="tenant", scope_id=c.actor.tenant_id)] if c.authorized else []
    async def revoke(**fields):
        grant = await lock(tenant_id=fields["tenant_id"], grant_id=fields["grant_id"])
        if grant.status != "active" or (grant.expires_at is not None and grant.expires_at <= fields["as_of"]):
            return None
        grant.status, grant.revoked_at = "revoked", fields["as_of"]
        grant.revoked_by_user_id = fields["revoked_by_user_id"]
        save("grant", grant.id, grant)
        return grant
    async def create(**fields):
        c.calls.append("create")
        grant = SimpleNamespace(id=uuid4(), **fields, status="active", revoked_at=None, revoked_by_user_id=None)
        save("grant", grant.id, grant)
        return grant
    c.repo = SimpleNamespace(
        session=c.session, get_grant_for_update=AsyncMock(side_effect=lock),
        list_active_grants=AsyncMock(side_effect=active), revoke_if_effective=AsyncMock(side_effect=revoke),
        create_grant=AsyncMock(side_effect=create), has_active_platform_membership=AsyncMock(return_value=True),
    )
    async def event(**fields):
        value = SimpleNamespace(id=uuid4(), **fields)
        save("audit", value.id, value)
        if c.fail_second_audit and len(rows("audit")) == 2:
            raise RuntimeError("private audit diagnostic")
        return value
    events = SimpleNamespace(session=c.session, create_event=AsyncMock(side_effect=event))
    projects = SimpleNamespace(
        session=c.session, get_active_project_for_update=AsyncMock(return_value=SimpleNamespace(
            id=c.project_id, tenant_id=c.actor.tenant_id, deleted_at=None,
        )),
    )
    c.service = ConstructionAccessMutationService(
        session=c.session, repository=c.repo, event_repository=events,
        project_repository=projects, now_provider=lambda: c.now,
    )
    class Ledger:
        session = c.session
        async def reserve_record(self, *, now, expires_at, **fields):
            c.calls.append("reserve")
            context = ("tenant_id", "principal_type", "principal_id", "operation", "key_hash")
            record = next((r for r in rows("ledger") if all(getattr(r, k) == fields[k] for k in context)), None)
            if record is not None and record.expires_at > now:
                return record, False
            record = SimpleNamespace(
                id=record.id if record else uuid4(), **fields, status="processing",
                created_at=now, expires_at=expires_at, completed_at=None,
                response_status=None, response_ciphertext=None,
            )
            save("ledger", record.id, record)
            return record, True
        async def complete_record(self, *, record, **fields):
            c.calls.append("complete")
            vars(record).update(fields, status="completed")
            save("ledger", record.id, record)
            if c.fail_complete:
                raise RuntimeError("private completion diagnostic")
            return record
    c.codec = ApiIdempotencyResponseCodec(encryption_key=Fernet.generate_key().decode())
    c.idem = ApiIdempotencyService(repository=Ledger(), response_codec=c.codec)
    c.workflow = ConstructionAccessMutationWorkflow(
        session=c.session, mutation_service=c.service,
        access_resolver=ConstructionAccessContextResolver(repository=c.repo, now_provider=lambda: c.now),
        idempotency_service=c.idem,
    )
    yield c
    c.db.close()


async def call(c, *, replacing=True, **changes):
    if not c.db.in_transaction:
        c.db.execute("BEGIN")
    values = dict(actor=c.actor, grant_id=c.original.id, idempotency_key=None, trace_id="grant-workflow")
    if replacing:
        values.update(role="SURVEYOR", scope_type="project", scope_id=c.project_id, expires_at=None)
    values.update(changes)
    method = c.workflow.replace_access if replacing else c.workflow.revoke_access
    return await method(**values)


def snapshot(c):
    return c.db.execute("SELECT kind,key,payload FROM state ORDER BY kind,key").fetchall()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "replace-key"])
async def test_replace_commits_history_audit_and_optional_encrypted_ledger_once(pipeline, key):
    c = pipeline
    result = await call(c, idempotency_key=key)
    assert set(result) == PUBLIC_GRANT_FIELDS and "never-public" not in json.dumps(result)
    assert result["id"] != str(c.original.id) and result["user_id"] == str(c.original.user_id)
    grants = c.rows("grant")
    assert len(grants) == 2 and {g.status for g in grants} == {"active", "revoked"}
    assert {e.event_type for e in c.rows("audit")} == {
        "construction_access_grant_created", "construction_access_grant_revoked",
    }
    c.session.commit.assert_awaited_once()
    assert len(c.rows("ledger")) == (0 if key is None else 1)
    if key is not None:
        record = c.rows("ledger")[0]
        assert record.operation == "construction.access_grant.replace"
        assert record.principal_id == c.actor.user_id and record.tenant_id == c.actor.tenant_id
        assert record.status == "completed" and c.codec.decrypt(record.response_ciphertext) == result
        assert result["id"].encode() not in record.response_ciphertext


@pytest.mark.asyncio
async def test_replay_returns_original_response_after_result_expiry_without_writes(pipeline):
    c = pipeline
    first = await call(c, idempotency_key="key", expires_at=c.now+timedelta(minutes=1))
    before = snapshot(c)
    c.now += timedelta(minutes=2)
    second = await call(c, idempotency_key="key", expires_at=c.now-timedelta(minutes=1))
    assert second == first and snapshot(c) == before
    assert c.repo.create_grant.await_count == 1 and c.repo.list_active_grants.await_count >= 2
    assert c.session.commit.await_count == 2


@pytest.mark.asyncio
async def test_unkeyed_revoke_retry_is_success_without_new_audit(pipeline):
    c = pipeline
    first = await call(c, replacing=False)
    before = snapshot(c)
    assert await call(c, replacing=False) == first and snapshot(c) == before
    assert len(c.rows("audit")) == 1 and c.rows("ledger") == []
    assert c.repo.revoke_if_effective.await_count == 1


@pytest.mark.asyncio
async def test_keyed_revoke_replay_does_not_call_mutation_again(pipeline):
    c = pipeline
    first = await call(c, replacing=False, idempotency_key="revoke-key")
    before = snapshot(c)
    assert await call(c, replacing=False, idempotency_key="revoke-key") == first
    assert snapshot(c) == before and c.repo.revoke_if_effective.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_workflow_failure_rolls_back_grants_audits_and_ledger(pipeline, failure):
    c = pipeline
    before = snapshot(c)
    setattr(c, {"audit": "fail_second_audit", "complete": "fail_complete", "commit": "fail_commit"}[failure], True)
    with pytest.raises(ConstructionGrantOperationError) as caught:
        await call(c, idempotency_key="failed-key")
    assert "private" not in str(caught.value) and snapshot(c) == before
    assert not c.db.in_transaction
    c.session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_same_key_different_request_is_rejected_without_new_changes(pipeline):
    c = pipeline
    await call(c, idempotency_key="key")
    before = snapshot(c)
    with pytest.raises(ApiIdempotencyKeyReusedError):
        await call(c, idempotency_key="key", role="EXECUTOR")
    assert snapshot(c) == before and c.repo.create_grant.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("replacing", [False, True])
async def test_replay_rechecks_current_authority(pipeline, replacing):
    c = pipeline
    await call(c, replacing=replacing, idempotency_key="key")
    before = snapshot(c)
    c.authorized = False
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await call(c, replacing=replacing, idempotency_key="key")
    assert snapshot(c) == before and c.session.commit.await_count == 1


@pytest.mark.asyncio
async def test_authorization_is_resolved_after_waiting_for_original_row(pipeline):
    c = pipeline
    before = snapshot(c)
    original = c.repo.get_grant_for_update.side_effect
    async def losing_access(**fields):
        c.authorized = False
        return await original(**fields)
    c.repo.get_grant_for_update.side_effect = losing_access
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await call(c, idempotency_key="key")
    assert snapshot(c) == before
    assert c.calls.index("reserve") < c.calls.index("lock") < c.calls.index("authorize")


@pytest.mark.asyncio
async def test_processing_reservation_returns_conflict_without_grant_mutation(pipeline):
    c = pipeline
    c.db.execute("BEGIN")
    await c.idem.reserve(
        tenant_id=c.actor.tenant_id, principal_type="user", principal_id=c.actor.user_id,
        operation="construction.access_grant.revoke", idempotency_key="processing",
        payload={"grant_id": str(c.original.id)}, now=c.now,
    )
    c.db.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionAccessIdempotencyInProgressError) as caught:
        await call(c, replacing=False, idempotency_key="processing")
    assert caught.value.status_code == 409 and snapshot(c) == before
    c.repo.revoke_if_effective.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("user_id", str(uuid4())), ("scope_id", str(uuid4())), ("status", "revoked"),
    ("id", "not-a-uuid"), ("internal_secret", "bad"), ("expires_at", "2030-01-01T00:00:00"),
])
async def test_invalid_cached_projection_is_rejected(pipeline, field, value):
    c = pipeline
    await call(c, idempotency_key="key")
    record = c.rows("ledger")[0]
    response = c.codec.decrypt(record.response_ciphertext)
    response[field] = value
    record.response_ciphertext = c.codec.encrypt(response)
    c.save("ledger", record.id, record)
    c.db.commit()
    before = snapshot(c)
    with pytest.raises(ConstructionGrantOperationError):
        await call(c, idempotency_key="key")
    assert snapshot(c) == before and c.session.commit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "   ", 123, "x"*256])
async def test_invalid_optional_key_is_rejected_before_reservation(pipeline, key):
    c = pipeline
    before = snapshot(c)
    with pytest.raises(ValueError):
        await call(c, idempotency_key=key)
    assert snapshot(c) == before and "reserve" not in c.calls


@pytest.mark.asyncio
async def test_second_replacement_with_new_key_conflicts_on_historical_original(pipeline):
    c = pipeline
    await call(c, idempotency_key="first")
    before = snapshot(c)
    with pytest.raises(ConstructionAccessGrantConflictError):
        await call(c, idempotency_key="second")
    assert snapshot(c) == before and len(c.rows("ledger")) == 1


def test_factory_shares_session_and_optional_idempotency(pipeline):
    c = pipeline
    without = build_construction_access_mutation_workflow(session=c.session)
    assert without.idempotency is None and without.service.repository.session is c.session
    with_key = build_construction_access_mutation_workflow(
        session=c.session, idempotency_encryption_key=Fernet.generate_key().decode(),
    )
    assert with_key.idempotency.repository.session is c.session
    assert with_key.resolver.repository.session is c.session
    with pytest.raises(ValueError):
        ConstructionAccessMutationWorkflow(
            session=c.session, mutation_service=c.service, access_resolver=with_key.resolver,
            idempotency_service=SimpleNamespace(repository=SimpleNamespace(session=object())),
        )


@pytest.mark.asyncio
async def test_authorization_is_resolved_after_waiting_for_target_project(pipeline):
    c = pipeline
    before = snapshot(c)
    async def project(**fields):
        c.authorized = False
        return SimpleNamespace(id=c.project_id, tenant_id=c.actor.tenant_id, deleted_at=None)
    c.service.project_repository.get_active_project_for_update.side_effect = project
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
        await call(c, idempotency_key="key")
    assert snapshot(c) == before and "create" not in c.calls


@pytest.mark.asyncio
async def test_replay_cannot_delegate_owner_from_unrelated_project(pipeline):
    c = pipeline
    await call(c, idempotency_key="key", role="OWNER")
    before = snapshot(c)
    async def narrower(**fields):
        return [
            SimpleNamespace(role="ADMIN", scope_type="project", scope_id=c.project_id),
            SimpleNamespace(role="OWNER", scope_type="project", scope_id=uuid4()),
        ]
    c.repo.list_active_grants.side_effect = narrower
    with pytest.raises(ConstructionPermissionDeniedError):
        await call(c, idempotency_key="key", role="OWNER")
    assert snapshot(c) == before and c.session.commit.await_count == 1


@pytest.mark.asyncio
async def test_equivalent_offset_and_role_normalization_replay_same_request(pipeline):
    c = pipeline
    deadline = c.now+timedelta(hours=2)
    first = await call(c, idempotency_key="key", expires_at=deadline)
    before = snapshot(c)
    second = await call(
        c, idempotency_key="key", role=" surveyor ", scope_type=" PROJECT ",
        expires_at=deadline.astimezone(timezone(timedelta(hours=3))),
    )
    assert first == second and snapshot(c) == before


@pytest.mark.asyncio
async def test_same_key_is_scoped_to_authenticated_principal(pipeline):
    c = pipeline
    await call(c, idempotency_key="key")
    before = snapshot(c)
    c.actor.user_id = uuid4()
    with pytest.raises(ConstructionAccessGrantConflictError):
        await call(c, idempotency_key="key")
    assert snapshot(c) == before and len(c.rows("ledger")) == 1


@pytest.mark.asyncio
async def test_expired_ledger_does_not_repeat_historical_replacement(pipeline):
    c = pipeline
    await call(c, idempotency_key="key")
    before = snapshot(c)
    c.now += timedelta(hours=25)
    with pytest.raises(ConstructionAccessGrantConflictError):
        await call(c, idempotency_key="key")
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_key_is_optional_when_workflow_has_no_idempotency_service(pipeline):
    c = pipeline
    c.workflow.idempotency = None
    before = snapshot(c)
    with pytest.raises(ConstructionGrantOperationError):
        await call(c, idempotency_key="key")
    assert snapshot(c) == before
    result = await call(c)
    assert result["status"] == "active" and c.rows("ledger") == []


@pytest.mark.asyncio
async def test_overlap_reported_at_commit_is_conflict_and_rolls_back_everything(pipeline):
    from sqlalchemy.exc import IntegrityError
    c = pipeline
    before = snapshot(c)
    driver = RuntimeError("private deferred diagnostic")
    driver.sqlstate = "23P01"
    driver.constraint_name = "ex_construction_access_grants_active_period"
    c.session.commit.side_effect = IntegrityError("private SQL", {}, driver)
    with pytest.raises(ConstructionAccessGrantConflictError):
        await call(c, idempotency_key="key")
    assert snapshot(c) == before and not c.db.in_transaction
