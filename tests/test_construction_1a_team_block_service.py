import json
import sqlite3
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from services.construction_grants import ConstructionGrantOperationError
from services.construction_permissions import ConstructionPermissionDeniedError
from services.construction_team_block import ConstructionTeamBlockService, build_construction_team_block_service


def grant_from_row(row):
    if row is None:
        return None
    values = dict(row)
    for name in ("id", "tenant_id", "user_id", "scope_id", "granted_by_user_id", "revoked_by_user_id"):
        values[name] = UUID(values[name]) if values[name] is not None else None
    for name in ("expires_at", "granted_at", "revoked_at"):
        values[name] = datetime.fromisoformat(values[name]) if values[name] is not None else None
    return SimpleNamespace(**values)


def snapshot(c):
    return {table: [tuple(row) for row in c.db.execute("SELECT * FROM "+table+" ORDER BY 1")]
            for table in ("grants", "events", "users", "memberships")}


@pytest.fixture
def block_case():
    c = SimpleNamespace(now=datetime(2026, 10, 7, 12, tzinfo=timezone.utc), events_attempted=0)
    c.actor = SimpleNamespace(user_id=uuid4(), tenant_id=uuid4(), status="active")
    c.target, c.foreign_tenant, c.other_user = uuid4(), uuid4(), uuid4()
    c.db = sqlite3.connect(":memory:", isolation_level=None)
    c.db.row_factory = sqlite3.Row
    c.db.executescript("""
        CREATE TABLE grants(
            id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT, role TEXT,
            scope_type TEXT, scope_id TEXT, status TEXT, expires_at TEXT,
            granted_by_user_id TEXT, granted_at TEXT, revoked_by_user_id TEXT, revoked_at TEXT
        );
        CREATE TABLE events(id INTEGER PRIMARY KEY, event_type TEXT, tenant_id TEXT,
                            user_id TEXT, entity_id TEXT, trace_id TEXT, payload TEXT);
        CREATE TABLE users(id TEXT PRIMARY KEY, tenant_id TEXT, status TEXT);
        CREATE TABLE memberships(id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT, role TEXT, status TEXT);
    """)
    def seed(user, role, scope, *, tenant=None, expires=None, revoked=False):
        tenant = tenant or c.actor.tenant_id
        identifier = uuid4()
        c.db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            str(identifier), str(tenant), str(user), role, scope,
            str(tenant if scope == "tenant" else uuid4()), "revoked" if revoked else "active",
            expires.isoformat() if expires else None, str(c.actor.user_id),
            (c.now-timedelta(days=2)).isoformat(),
            str(c.actor.user_id) if revoked else None, (c.now-timedelta(days=1)).isoformat() if revoked else None,
        ))
        return identifier
    c.authority_id = seed(c.actor.user_id, "OWNER", "tenant")
    c.expected = {
        seed(c.target, "ADMIN", "tenant"), seed(c.target, "SURVEYOR", "project"),
        seed(c.target, "OBSERVER", "document", expires=c.now+timedelta(hours=1)),
    }
    seed(c.target, "EXECUTOR", "project", expires=c.now)
    seed(c.target, "ESTIMATOR", "project", revoked=True)
    seed(c.target, "ADMIN", "tenant", tenant=c.foreign_tenant)
    seed(c.other_user, "EXECUTOR", "project")
    c.db.execute("INSERT INTO users VALUES(?,?,?)", (str(c.target), str(c.actor.tenant_id), "active"))
    c.db.execute("INSERT INTO memberships VALUES(?,?,?,?,?)", (
        str(uuid4()), str(c.actor.tenant_id), str(c.target), "client", "active",
    ))
    c.session = SimpleNamespace(
        in_transaction=lambda: c.db.in_transaction, rollback=AsyncMock(side_effect=c.db.rollback),
        commit=AsyncMock(side_effect=AssertionError("The workflow owns commit.")),
    )
    async def list_active(**values):
        assert values["tenant_id"] == c.actor.tenant_id
        if "scope_type" not in values:
            return [grant_from_row(row) for row in c.db.execute(
                "SELECT * FROM grants WHERE tenant_id=? AND user_id=? AND status='active' "
                "AND revoked_at IS NULL AND revoked_by_user_id IS NULL AND (expires_at IS NULL OR expires_at>?)",
                (str(values["tenant_id"]),str(values["user_id"]),values["as_of"].isoformat()))]
        assert values["user_id"] == c.actor.user_id
        assert values["scope_type"] == "tenant" and values["scope_id"] == c.actor.tenant_id
        return [grant_from_row(row) for row in c.db.execute(
            "SELECT * FROM grants WHERE tenant_id=? AND user_id=? AND scope_type=? AND scope_id=? "
            "AND status='active' AND (expires_at IS NULL OR expires_at>?)",
            (str(values["tenant_id"]), str(values["user_id"]), "tenant", str(values["scope_id"]),
             values["as_of"].isoformat()),
        )]
    async def lock(**values):
        return grant_from_row(c.db.execute(
            "SELECT * FROM grants WHERE tenant_id=? AND id=?",
            (str(values["tenant_id"]), str(values["grant_id"])),
        ).fetchone())
    async def bulk(**values):
        assert values["tenant_id"] == c.actor.tenant_id and values["revoked_by_user_id"] == c.actor.user_id
        now = values["as_of"].isoformat()
        ids = [row[0] for row in c.db.execute(
            "SELECT id FROM grants WHERE tenant_id=? AND user_id=? AND status='active' "
            "AND revoked_at IS NULL AND revoked_by_user_id IS NULL AND (expires_at IS NULL OR expires_at>?)",
            (str(values["tenant_id"]), str(values["user_id"]), now),
        )]
        for identifier in ids:
            c.db.execute("UPDATE grants SET status='revoked', revoked_at=?, revoked_by_user_id=? WHERE id=?",
                         (now, str(values["revoked_by_user_id"]), identifier))
        return [grant_from_row(c.db.execute("SELECT * FROM grants WHERE id=?", (identifier,)).fetchone())
                for identifier in reversed(ids)]
    async def event(**values):
        assert values["entity_type"] == "construction_access_grant" and values["platform"] == "api"
        c.events_attempted += 1
        c.db.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?)", (
            values["event_type"], str(values["tenant_id"]), str(values["user_id"]),
            str(values["entity_id"]), values["trace_id"], json.dumps(values["payload"], sort_keys=True),
        ))
        return SimpleNamespace(**values)
    c.repo = SimpleNamespace(
        session=c.session, list_active_grants=AsyncMock(side_effect=list_active),
        get_grant_for_update=AsyncMock(side_effect=lock),
        revoke_effective_user_grants=AsyncMock(side_effect=bulk),
        has_active_platform_membership=AsyncMock(), create_grant=AsyncMock(),
    )
    c.event_repo = SimpleNamespace(session=c.session, create_event=AsyncMock(side_effect=event))
    c.service = ConstructionTeamBlockService(
        session=c.session, repository=c.repo, event_repository=c.event_repo, now_provider=lambda: c.now,
    )
    c.db.execute("BEGIN")
    yield c
    c.db.close()


async def block(c, **changes):
    values = dict(actor=c.actor, user_id=c.target, trace_id="team-block-test")
    values.update(changes)
    return await c.service.block_access(**values)


def no_platform_changes(c, before):
    current = snapshot(c)
    assert current["users"] == before["users"] and current["memberships"] == before["memberships"]
    c.repo.has_active_platform_membership.assert_not_called()
    c.repo.create_grant.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["OWNER", "ADMIN"])
async def test_block_all_own_tenant_scopes_with_exact_existing_audit_and_history(block_case, role):
    c = block_case
    c.db.execute("UPDATE grants SET role=? WHERE id=?", (role, str(c.authority_id)))
    before = snapshot(c)
    result = await block(c)
    assert result.user_id == c.target and set(result.revoked_grant_ids) == c.expected
    assert result.revoked_grant_ids == tuple(sorted(c.expected, key=lambda value: value.int))
    with pytest.raises(FrozenInstanceError):
        result.user_id = uuid4()
    locked_ids = [call.kwargs["grant_id"] for call in c.repo.get_grant_for_update.await_args_list]
    assert locked_ids == [c.authority_id, *sorted(c.expected,key=lambda value:value.int)]
    after = snapshot(c)
    for old, new in zip(before["grants"], after["grants"]):
        assert old[:6] == new[:6] and old[7:10] == new[7:10]
        if UUID(old[0]) in c.expected:
            assert old[6] == "active" and new[6] == "revoked"
            assert new[10:] == (str(c.actor.user_id), c.now.isoformat())
        else:
            assert old == new
    assert len(after["events"]) == 3 and c.db.in_transaction
    for row in after["events"]:
        assert row[1:4] == ("construction_access_grant_revoked", str(c.actor.tenant_id), str(c.actor.user_id))
        assert UUID(row[4]) in c.expected and row[5] == "team-block-test"
        payload = json.loads(row[6])
        assert payload["operation"] == "revoke" and payload["team_action"] == "block"
        assert payload["target_user_id"] == str(c.target)
        assert payload["changes"] == [
            {"field": "status", "old_value": "active", "new_value": "revoked"},
            {"field": "revoked_by_user_id", "old_value": None, "new_value": str(c.actor.user_id)},
            {"field": "revoked_at", "old_value": None, "new_value": c.now.isoformat()},
        ]
    c.session.rollback.assert_not_called()
    no_platform_changes(c, before)


@pytest.mark.asyncio
async def test_authorized_repeat_is_no_op_without_extra_audit(block_case):
    c = block_case
    await block(c)
    before = snapshot(c)
    result = await block(c)
    assert result.user_id == c.target and result.revoked_grant_ids == ()
    assert snapshot(c) == before and c.event_repo.create_event.await_count == 3
    c.session.rollback.assert_not_called()
    no_platform_changes(c, before)


@pytest.mark.asyncio
async def test_self_block_uses_locked_pre_operation_authority(block_case):
    c = block_case
    before = snapshot(c)
    result = await block(c, user_id=c.actor.user_id)
    assert result.revoked_grant_ids == (c.authority_id,)
    assert all(row[6] == old[6] for row, old in zip(snapshot(c)["grants"], before["grants"])
               if UUID(row[0]) != c.authority_id)
    assert len(snapshot(c)["events"]) == 1
    no_platform_changes(c, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inactive", "actor_uuid", "tenant_uuid", "no_transaction",
                                    "project_admin", "observer", "foreign_scope", "no_grants", "lock_missing",
                                    "lock_revoked"])
async def test_block_denies_without_tenant_wide_current_authority(block_case, failure):
    c = block_case
    if failure == "inactive":
        c.actor.status = "suspended"
    elif failure == "actor_uuid":
        c.actor.user_id = "invalid"
    elif failure == "tenant_uuid":
        c.actor.tenant_id = None
    elif failure == "no_transaction":
        c.db.rollback()
    elif failure in ("project_admin", "observer", "foreign_scope"):
        if failure == "project_admin":
            c.db.execute("UPDATE grants SET role='ADMIN', scope_type='project' WHERE id=?", (str(c.authority_id),))
        elif failure == "observer":
            c.db.execute("UPDATE grants SET role='OBSERVER' WHERE id=?", (str(c.authority_id),))
        else:
            c.db.execute("UPDATE grants SET scope_id=? WHERE id=?", (str(uuid4()), str(c.authority_id)))
    elif failure == "no_grants":
        c.repo.list_active_grants.side_effect = None
        c.repo.list_active_grants.return_value = []
    elif failure == "lock_missing":
        c.repo.get_grant_for_update.side_effect = None
        c.repo.get_grant_for_update.return_value = None
    else:
        original = c.repo.get_grant_for_update.side_effect
        async def revoked(**values):
            grant = await original(**values)
            grant.status = "revoked"
            return grant
        c.repo.get_grant_for_update.side_effect = revoked
    if c.db.in_transaction:
        c.db.commit()
        c.db.execute("BEGIN")
    before = snapshot(c)
    with pytest.raises(ConstructionPermissionDeniedError):
        await block(c)
    assert snapshot(c) == before
    c.repo.revoke_effective_user_grants.assert_not_called()
    c.event_repo.create_event.assert_not_called()
    c.session.rollback.assert_awaited_once()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, "invalid", True])
async def test_block_rejects_invalid_target_before_permission_queries(block_case, target):
    c = block_case
    before = snapshot(c)
    with pytest.raises(ValueError):
        await block(c, user_id=target)
    assert snapshot(c) == before
    c.repo.list_active_grants.assert_not_called()
    c.repo.revoke_effective_user_grants.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["bulk", "first_audit", "second_audit", "audit_value_error"])
async def test_block_rolls_back_all_grants_and_actual_audit_rows_on_failure(block_case, failure):
    c = block_case
    before = snapshot(c)
    if failure == "bulk":
        original = c.repo.revoke_effective_user_grants.side_effect
        async def fail(**values):
            await original(**values)
            raise RuntimeError("private database diagnostic")
        c.repo.revoke_effective_user_grants.side_effect = fail
    else:
        original = c.event_repo.create_event.side_effect
        async def fail(**values):
            result = await original(**values)
            if c.events_attempted == (1 if failure in ("first_audit", "audit_value_error") else 2):
                exception = ValueError if failure == "audit_value_error" else RuntimeError
                raise exception("private database diagnostic")
            return result
        c.event_repo.create_event.side_effect = fail
    with pytest.raises(ConstructionGrantOperationError) as caught:
        await block(c)
    assert "private" not in str(caught.value)
    assert snapshot(c) == before
    c.session.rollback.assert_awaited_once()
    no_platform_changes(c, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", ["list", "lock", "bulk", "audit"])
async def test_authority_expiration_during_wait_denies_and_rolls_back(block_case, wait):
    c = block_case
    c.db.execute("UPDATE grants SET expires_at=? WHERE id=?", (
        (c.now+timedelta(seconds=1)).isoformat(), str(c.authority_id),
    ))
    c.db.commit()
    c.db.execute("BEGIN")
    before = snapshot(c)
    method = {"list": c.repo.list_active_grants, "lock": c.repo.get_grant_for_update,
              "bulk": c.repo.revoke_effective_user_grants, "audit": c.event_repo.create_event}[wait]
    original = method.side_effect
    async def advance(**values):
        result = await original(**values)
        c.now += timedelta(seconds=1)
        return result
    method.side_effect = advance
    with pytest.raises(ConstructionPermissionDeniedError):
        await block(c)
    assert snapshot(c) == before
    if wait in ("list", "lock"):
        c.repo.revoke_effective_user_grants.assert_not_called()
    else:
        c.repo.revoke_effective_user_grants.assert_awaited_once()
    c.session.rollback.assert_awaited_once()
    no_platform_changes(c, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "tenant", "user", "status", "actor", "time", "duplicate", "shape", "expired",
    "role", "scope_id", "scope_type", "naive_expiry",
])
async def test_invalid_bulk_projection_rolls_back_before_audit(block_case, failure):
    c = block_case
    before = snapshot(c)
    original = c.repo.revoke_effective_user_grants.side_effect
    async def invalid(**values):
        rows = await original(**values)
        if failure == "tenant":
            rows[0].tenant_id = uuid4()
        elif failure == "user":
            rows[0].user_id = uuid4()
        elif failure == "status":
            rows[0].status = "active"
        elif failure == "actor":
            rows[0].revoked_by_user_id = uuid4()
        elif failure == "time":
            rows[0].revoked_at = c.now+timedelta(seconds=1)
        elif failure == "duplicate":
            rows.append(rows[0])
        elif failure == "expired":
            rows[0].expires_at = c.now
        elif failure == "role":
            rows[0].role = "CUSTOM"
        elif failure == "scope_id":
            rows[0].scope_id = "invalid"
        elif failure == "scope_type":
            rows[0].scope_type = "unknown"
        elif failure == "naive_expiry":
            rows[0].expires_at = c.now.replace(tzinfo=None)
        else:
            return {"grants": rows}
        return rows
    c.repo.revoke_effective_user_grants.side_effect = invalid
    with pytest.raises(ConstructionGrantOperationError):
        await block(c)
    assert snapshot(c) == before
    c.event_repo.create_event.assert_not_called()
    no_platform_changes(c, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["clock", "expiry"])
async def test_invalid_internal_time_metadata_fails_closed(block_case, failure):
    c = block_case
    before = snapshot(c)
    if failure == "clock":
        c.service.now_provider = lambda: c.now.replace(tzinfo=None)
    else:
        original = c.repo.get_grant_for_update.side_effect
        async def invalid(**values):
            result = await original(**values)
            result.expires_at = c.now.replace(tzinfo=None)
            return result
        c.repo.get_grant_for_update.side_effect = invalid
    with pytest.raises(ConstructionGrantOperationError):
        await block(c)
    assert snapshot(c) == before
    c.repo.revoke_effective_user_grants.assert_not_called()
    c.event_repo.create_event.assert_not_called()


def test_block_factory_uses_one_session_and_rejects_mixed_wiring(block_case):
    c = block_case
    service = build_construction_team_block_service(c.session, now_provider=lambda: c.now)
    assert service.repository.session is c.session
    assert service.audit_logger.event_repository.session is c.session
    with pytest.raises(ValueError):
        ConstructionTeamBlockService(session=c.session, repository=c.repo,
                                     event_repository=SimpleNamespace(session=object()))
