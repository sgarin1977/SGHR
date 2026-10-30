"""OWNER survival, atomic rollback and native SQL contracts; no external operations."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from database.repositories.construction.construction_owner_boundary import (
    ConstructionOwnerBoundaryRepository, EffectiveConstructionOwner,
)
from services.construction.construction_owner_boundary import (
    ConstructionLastOwnerChangeDeniedError,
    require_owner_survival_for_grants,
)
from services.construction.construction_grants import ConstructionGrantOperationError, ConstructionAccessGrantService
from services.construction.construction_permissions import ConstructionPermissionDeniedError
from test_construction_1a_access_mutation_workflow import pipeline, call, snapshot
from test_construction_1a_team_block_service import block_case, block, snapshot as block_snapshot
from test_construction_1a_team_block_workflow import block_pipeline, run
from test_construction_1a_team_block_http import block_http, post
from test_construction_1a_access_mutation_service import case, context
from services.construction.construction_permissions import ConstructionGrantContext


@pytest.fixture
def owner_pipeline(pipeline, monkeypatch):
    c = pipeline
    c.original.role = "OWNER"
    c.original.scope_type = "tenant"
    c.original.scope_id = c.actor.tenant_id
    c.original.user_id = c.actor.user_id
    c.save("grant", c.original.id, c.original)
    c.db.commit()
    c.eligible = {c.actor.user_id}
    c.owner_reads = 0

    async def local(repository, *, tenant_id, as_of):
        assert repository.session is c.session and tenant_id == c.actor.tenant_id
        c.owner_reads += 1
        rows = [
            row for row in c.rows("grant")
            if row.tenant_id == tenant_id and row.role == "OWNER"
            and row.scope_type == "tenant" and row.scope_id == tenant_id
            and row.status == "active" and row.revoked_at is None
            and row.revoked_by_user_id is None and row.user_id in c.eligible
            and (row.expires_at is None or row.expires_at > as_of)
        ]
        return tuple(EffectiveConstructionOwner(
            row.id, tenant_id, row.user_id, row.expires_at,
        ) for row in rows)

    monkeypatch.setattr(
        ConstructionOwnerBoundaryRepository, "list_effective_owners", local,
    )
    def add_owner(*, eligible=True, expiry=None):
        other = deepcopy(c.original)
        other.id, other.user_id = uuid4(), uuid4()
        other.expires_at = expiry
        c.save("grant", other.id, other)
        if eligible:
            c.eligible.add(other.user_id)
        c.db.commit()
        return other
    c.add_owner = add_owner
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["revoke", "downgrade", "move"])
@pytest.mark.parametrize("key", [None, "owner-boundary"])
async def test_last_owner_change_rolls_back_history_audit_and_receipt(owner_pipeline, operation, key):
    c = owner_pipeline
    before = snapshot(c)
    with pytest.raises(ConstructionLastOwnerChangeDeniedError):
        if operation == "revoke":
            await call(c, replacing=False, idempotency_key=key)
        elif operation == "downgrade":
            await call(c, role="ADMIN", scope_type="tenant",
                       scope_id=c.actor.tenant_id, idempotency_key=key)
        else:
            await call(c, idempotency_key=key)
    assert snapshot(c) == before
    c.session.commit.assert_not_awaited()
    assert not c.db.in_transaction


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["revoke", "downgrade"])
async def test_another_eligible_owner_allows_ordinary_change(owner_pipeline, operation):
    c = owner_pipeline
    other = c.add_owner()
    if operation == "revoke":
        result = await call(c, replacing=False)
    else:
        result = await call(c, role="ADMIN", scope_type="tenant", scope_id=c.actor.tenant_id)
    assert result["status"] == ("revoked" if operation == "revoke" else "active")
    assert any(g.id == other.id and g.status == "active" for g in c.rows("grant"))
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [None, datetime(2030, 1, 1, tzinfo=timezone.utc)])
async def test_owner_to_owner_replacement_preserves_last_owner(owner_pipeline, expiry):
    c = owner_pipeline
    result = await call(
        c, role="OWNER", scope_type="tenant", scope_id=c.actor.tenant_id,
        expires_at=expiry, idempotency_key="owner-expiry",
    )
    assert result["role"] == "OWNER" and result["status"] == "active"
    assert result["id"] != str(c.original.id)
    assert len(c.rows("audit")) == 2
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ineligible", "expired"])
async def test_ineligible_or_expired_other_owner_does_not_satisfy_boundary(owner_pipeline, failure):
    c = owner_pipeline
    c.add_owner(eligible=failure != "ineligible",
                expiry=c.now if failure == "expired" else None)
    before = snapshot(c)
    with pytest.raises(ConstructionLastOwnerChangeDeniedError):
        await call(c, replacing=False)
    assert snapshot(c) == before
    c.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_expiry_during_receipt_completion_rolls_back_everything(owner_pipeline):
    c = owner_pipeline
    c.add_owner(expiry=c.now + timedelta(seconds=1))
    before = snapshot(c)
    complete = c.idem.complete
    async def waited(**kwargs):
        result = await complete(**kwargs)
        c.now += timedelta(seconds=2)
        return result
    c.idem.complete = waited
    with pytest.raises(ConstructionLastOwnerChangeDeniedError):
        await call(
            c, role="ADMIN", scope_type="tenant", scope_id=c.actor.tenant_id,
            idempotency_key="waited-receipt",
        )
    assert c.owner_reads >= 2
    assert snapshot(c) == before
    c.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_revoked_owner_noop_does_not_require_a_new_owner_check(owner_pipeline):
    c = owner_pipeline
    c.original.status = "revoked"
    c.original.revoked_at = c.now
    c.original.revoked_by_user_id = c.actor.user_id
    c.save("grant", c.original.id, c.original)
    c.db.commit()
    before = snapshot(c)
    await call(c, replacing=False)
    assert snapshot(c) == before and c.owner_reads == 0


@pytest.mark.asyncio
async def test_block_last_owner_http_returns_forbidden_and_rolls_back(block_http):
    c = block_http
    before = block_snapshot(c)
    response = await post(c, identifier=str(c.actor.user_id))
    assert response.status_code == 403, response.text
    assert block_snapshot(c) == before
    c.session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_self_block_allowed_when_another_active_member_is_owner(block_pipeline):
    c = block_pipeline
    c.db.execute(
        "UPDATE grants SET role='OWNER' WHERE tenant_id=? AND user_id=? AND scope_type='tenant'",
        (str(c.actor.tenant_id), str(c.target)),
    )
    c.db.commit()
    c.db.execute("BEGIN")
    before = block_snapshot(c)
    response = await run(c, user_id=c.actor.user_id)
    assert response["revoked_grant_ids"] == [str(c.authority_id)]
    assert block_snapshot(c)["users"] == before["users"]
    assert block_snapshot(c)["memberships"] == before["memberships"]
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_block_rolls_back_when_other_owner_identity_is_blocked(block_case):
    c = block_case
    c.db.execute(
        "UPDATE grants SET role='OWNER' WHERE tenant_id=? AND user_id=? AND scope_type='tenant'",
        (str(c.actor.tenant_id), str(c.target)),
    )
    c.db.execute("UPDATE users SET status='blocked' WHERE id=?", (str(c.target),))
    c.db.commit()
    c.db.execute("BEGIN")
    before = block_snapshot(c)
    with pytest.raises(ConstructionLastOwnerChangeDeniedError):
        await block(c, user_id=c.actor.user_id)
    assert block_snapshot(c) == before


@pytest.mark.asyncio
async def test_legacy_revoke_checks_owner_boundary_before_its_commit(case, monkeypatch):
    c = case
    c.grant.role = "OWNER"
    c.grant.scope_type = "tenant"
    c.grant.scope_id = c.actor.tenant_id
    c.original = deepcopy(vars(c.grant))

    async def revoke_grant(**kwargs):
        c.grant.status = "revoked"
        c.grant.revoked_at = kwargs["revoked_at"]
        c.grant.revoked_by_user_id = kwargs["revoked_by_user_id"]
        return c.grant
    c.repo.revoke_grant = AsyncMock(side_effect=revoke_grant)
    async def no_owner(repository, **kwargs):
        assert repository.session is c.session
        return ()
    monkeypatch.setattr(
        ConstructionOwnerBoundaryRepository, "list_effective_owners", no_owner,
    )
    legacy = ConstructionAccessGrantService(
        session=c.session, repository=c.repo, event_repository=c.event_repo,
        now_provider=lambda: c.now,
    )
    with pytest.raises(ConstructionLastOwnerChangeDeniedError):
        await legacy.revoke_access(
            actor=c.actor, access_context=c.access, grant_id=c.grant.id,
        )
    assert vars(c.grant) == c.original and c.events == []
    c.session.commit.assert_not_awaited()


def result_mapping(*, one=None, rows=None):
    return NS(mappings=lambda: NS(
        one=lambda: one, all=lambda: rows if rows is not None else [],
    ))


def postgres_session(*, context=None, rows=None):
    transaction = NS(is_active=True)
    session = NS(
        no_autoflush=nullcontext(),
        in_transaction=lambda: True,
        get_transaction=lambda: transaction,
        get_nested_transaction=lambda: None,
        get_bind=lambda: NS(dialect=NS(name="postgresql")),
        execute=AsyncMock(side_effect=[
            result_mapping(one=context or {
                "read_only": "off", "isolation": "read committed", "ownership_locked": True,
            }),
            result_mapping(rows=rows or []),
        ]),
    )
    return session


@pytest.mark.asyncio
async def test_native_query_uses_canonical_membership_and_only_grant_row_locks():
    tenant, user, identifier = uuid4(), uuid4(), uuid4()
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    session = postgres_session(rows=[{
        "grant_id": identifier, "tenant_id": tenant, "user_id": user, "expires_at": None,
    }])
    result = await ConstructionOwnerBoundaryRepository(session).list_effective_owners(
        tenant_id=tenant, as_of=now,
    )
    assert result == (EffectiveConstructionOwner(identifier, tenant, user, None),)
    statement = session.execute.await_args_list[1].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "users.status" in sql and "user_roles.status" in sql
    assert "user_roles.tenant_id" in sql and "users.tenant_id" in sql
    assert "construction_access_grants.revoked_at is null" in sql
    assert "construction_access_grants.revoked_by_user_id is null" in sql
    assert "for update of construction_access_grants" in sql
    assert "for update of users" not in sql
    assert " limit " not in sql
    assert tenant in compiled.params.values()
    assert "OWNER" in compiled.params.values()
    assert now in compiled.params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("ownership_locked", False), ("read_only", "on"),
    ("isolation", "repeatable read"),
])
async def test_missing_lock_or_wrong_transaction_denies_before_owner_query(field, value):
    context = {"read_only": "off", "isolation": "read committed", "ownership_locked": True}
    context[field] = value
    session = postgres_session(context=context)
    with pytest.raises(ValueError):
        await ConstructionOwnerBoundaryRepository(session).list_effective_owners(
            tenant_id=uuid4(), as_of=datetime.now(timezone.utc),
        )
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_owner_expiring_during_query_is_not_accepted(monkeypatch):
    tenant, user = uuid4(), uuid4()
    clock = NS(now=datetime(2026, 10, 28, tzinfo=timezone.utc))
    expiry = clock.now + timedelta(seconds=1)
    async def waited(repository, **kwargs):
        clock.now = expiry
        return (EffectiveConstructionOwner(uuid4(), tenant, user, expiry),)
    monkeypatch.setattr(
        ConstructionOwnerBoundaryRepository, "list_effective_owners", waited,
    )
    with pytest.raises(ConstructionLastOwnerChangeDeniedError):
        await require_owner_survival_for_grants(
            session=object(), tenant_id=tenant,
            grants=(NS(tenant_id=tenant, role="OWNER", scope_type="tenant", scope_id=tenant),),
            now_provider=lambda: clock.now,
        )


@pytest.mark.asyncio
async def test_nonowner_change_never_queries_owner_roster(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No OWNER mutation.")
    monkeypatch.setattr(
        ConstructionOwnerBoundaryRepository, "list_effective_owners", forbidden,
    )
    tenant = uuid4()
    await require_owner_survival_for_grants(
        session=object(), tenant_id=tenant,
        grants=(NS(tenant_id=tenant, role="ADMIN", scope_type="tenant", scope_id=tenant),),
        now_provider=lambda: datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_owner_evidence_failure_is_sanitized_and_never_treated_as_permission(monkeypatch):
    async def failure(*args, **kwargs):
        raise RuntimeError("private database diagnostic")
    monkeypatch.setattr(
        ConstructionOwnerBoundaryRepository, "list_effective_owners", failure,
    )
    tenant = uuid4()
    with pytest.raises(ConstructionGrantOperationError) as caught:
        await require_owner_survival_for_grants(
            session=object(), tenant_id=tenant,
            grants=(NS(tenant_id=tenant, role="OWNER", scope_type="tenant", scope_id=tenant),),
            now_provider=lambda: datetime.now(timezone.utc),
        )
    assert "private" not in str(caught.value)
