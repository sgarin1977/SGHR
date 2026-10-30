from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.construction.construction_grants import ConstructionGrantOperationError
from services.construction.construction_permissions import ConstructionPermissionDeniedError
from services.construction.construction_team_block import ConstructionTeamBlockResult
from services.construction.construction_team_block_workflow import (
    PUBLIC_TEAM_BLOCK_FIELDS, ConstructionTeamBlockWorkflow, build_construction_team_block_workflow,
)
from test_construction_1a_team_block_service import block_case, no_platform_changes, snapshot


@pytest.fixture
def block_pipeline(block_case):
    c = block_case
    c.fail_commit = None
    c.at_commit = None
    async def commit():
        c.at_commit = snapshot(c)
        if c.fail_commit is not None:
            raise c.fail_commit("private commit diagnostic")
        c.db.commit()
    c.session.commit.side_effect = commit
    c.workflow = ConstructionTeamBlockWorkflow(session=c.session, block_service=c.service)
    return c


async def run(c, **changes):
    values = dict(actor=c.actor, user_id=c.target, trace_id="team-block-workflow-test")
    values.update(changes)
    return await c.workflow.block_access(**values)


@pytest.mark.asyncio
async def test_workflow_commits_revocations_and_exact_audit_once(block_pipeline):
    c = block_pipeline
    before = snapshot(c)
    response = await run(c)
    assert set(response) == PUBLIC_TEAM_BLOCK_FIELDS
    assert response == {
        "user_id": str(c.target),
        "revoked_grant_ids": [str(value) for value in sorted(c.expected, key=lambda value: value.int)],
    }
    assert len(c.at_commit["events"]) == 3
    assert all(row[6] == "revoked" for row in c.at_commit["grants"] if row[0] in response["revoked_grant_ids"])
    assert all(row[5] == "team-block-workflow-test" for row in c.at_commit["events"])
    assert not c.db.in_transaction
    committed = snapshot(c)
    c.db.rollback()
    assert snapshot(c) == committed
    assert committed["users"] == before["users"] and committed["memberships"] == before["memberships"]
    c.repo.has_active_platform_membership.assert_not_called()
    c.repo.create_grant.assert_not_called()
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_workflow_authorized_repeat_commits_no_op_without_extra_audit(block_pipeline):
    c = block_pipeline
    await run(c)
    before = snapshot(c)
    c.db.execute("BEGIN")
    response = await run(c)
    assert response == {"user_id": str(c.target), "revoked_grant_ids": []}
    assert snapshot(c) == before
    assert c.session.commit.await_count == 2 and c.event_repo.create_event.await_count == 3
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_repeat_checks_current_rights_and_denies_after_authority_revoke(block_pipeline):
    c = block_pipeline
    await run(c)
    c.db.execute("UPDATE grants SET status='revoked' WHERE id=?", (str(c.authority_id),))
    before = snapshot(c)
    c.db.execute("BEGIN")
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c)
    assert snapshot(c) == before
    assert c.session.commit.await_count == 1 and c.repo.revoke_effective_user_grants.await_count == 1
    assert c.event_repo.create_event.await_count == 3


@pytest.mark.asyncio
async def test_workflow_last_owner_self_block_never_commits(block_pipeline):
    c = block_pipeline
    before = snapshot(c)
    with pytest.raises(ConstructionPermissionDeniedError):
        await run(c, user_id=c.actor.user_id)
    assert snapshot(c) == before and not c.db.in_transaction
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_later_block_revokes_newly_issued_access_without_restoring_old_grants(block_pipeline):
    c = block_pipeline
    await run(c)
    old_state = snapshot(c)
    previous = list(c.db.execute("SELECT * FROM grants WHERE id=?", (str(next(iter(c.expected))),)).fetchone())
    new_id = uuid4()
    previous[0], previous[6], previous[9], previous[10], previous[11] = str(new_id), "active", c.now.isoformat(), None, None
    c.db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", previous)
    c.db.execute("BEGIN")
    response = await run(c)
    assert response == {"user_id": str(c.target), "revoked_grant_ids": [str(new_id)]}
    current = snapshot(c)
    assert all(row in current["grants"] for row in old_state["grants"])
    assert len(current["events"]) == 4 and c.session.commit.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [RuntimeError, ValueError])
async def test_commit_failure_rolls_back_flushed_revocations_and_audit(block_pipeline, exception):
    c = block_pipeline
    before = snapshot(c)
    c.fail_commit = exception
    with pytest.raises(ConstructionGrantOperationError) as caught:
        await run(c)
    assert "private" not in str(caught.value)
    assert len(c.at_commit["events"]) == 3
    assert all(row[6] == "revoked" for row in c.at_commit["grants"] if row[0] in {str(value) for value in c.expected})
    assert snapshot(c) == before and not c.db.in_transaction
    c.session.commit.assert_awaited_once()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_audit_failure_never_commits(block_pipeline):
    c = block_pipeline
    before = snapshot(c)
    original = c.event_repo.create_event.side_effect
    async def failure(**fields):
        await original(**fields)
        if c.events_attempted == 2:
            raise RuntimeError("private audit diagnostic")
    c.event_repo.create_event.side_effect = failure
    with pytest.raises(ConstructionGrantOperationError):
        await run(c)
    assert snapshot(c) == before
    c.session.commit.assert_not_called()
    assert c.session.rollback.await_count >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["type", "user", "ids_type", "id", "duplicate", "order"])
async def test_invalid_service_result_rolls_back_before_commit(block_pipeline, failure):
    c = block_pipeline
    before = snapshot(c)
    original = c.service.block_access
    async def invalid(**fields):
        result = await original(**fields)
        if failure == "type":
            return {"user_id": str(c.target), "secret": "private result diagnostic"}
        if failure == "user":
            return ConstructionTeamBlockResult(uuid4(), result.revoked_grant_ids)
        if failure == "ids_type":
            return ConstructionTeamBlockResult(c.target, list(result.revoked_grant_ids))
        if failure == "id":
            return ConstructionTeamBlockResult(c.target, ("private result diagnostic",))
        if failure == "duplicate":
            return ConstructionTeamBlockResult(c.target, (result.revoked_grant_ids[0],)*2)
        return ConstructionTeamBlockResult(c.target, tuple(reversed(result.revoked_grant_ids)))
    c.service.block_access = AsyncMock(side_effect=invalid)
    with pytest.raises(ConstructionGrantOperationError) as caught:
        await run(c)
    assert "private" not in str(caught.value)
    assert snapshot(c) == before
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["inactive", "actor_id", "tenant_id", "target", "no_transaction"])
async def test_invalid_context_denies_before_service_or_commit(block_pipeline, failure):
    c = block_pipeline
    c.service.block_access = AsyncMock()
    values = {}
    if failure == "inactive":
        c.actor.status = "suspended"
    elif failure == "actor_id":
        c.actor.user_id = "invalid"
    elif failure == "tenant_id":
        c.actor.tenant_id = None
    elif failure == "target":
        values["user_id"] = "invalid"
    else:
        c.db.rollback()
    before = snapshot(c)
    with pytest.raises((ConstructionPermissionDeniedError, ConstructionGrantOperationError, ValueError)):
        await run(c, **values)
    assert snapshot(c) == before
    c.service.block_access.assert_not_called()
    c.session.commit.assert_not_called()
    c.session.rollback.assert_awaited_once()


@pytest.mark.parametrize("failure", ["service_session", "repository_session", "audit_session", "service_method",
                                    "commit", "rollback", "transaction"])
def test_workflow_rejects_mixed_or_invalid_wiring(block_pipeline, failure):
    c = block_pipeline
    if failure == "service_session":
        c.service.session = object()
    elif failure == "repository_session":
        c.repo.session = object()
    elif failure == "audit_session":
        c.event_repo.session = object()
    elif failure == "service_method":
        c.service.block_access = None
    else:
        setattr(c.session, {"transaction": "in_transaction"}.get(failure, failure), None)
    with pytest.raises(ValueError):
        ConstructionTeamBlockWorkflow(session=c.session, block_service=c.service)


def test_workflow_factory_reuses_one_session_and_clock(block_pipeline):
    c = block_pipeline
    workflow = build_construction_team_block_workflow(session=c.session, now_provider=lambda: c.now)
    assert workflow.session is c.session and workflow.service.session is c.session
    assert workflow.service.repository.session is c.session
    assert workflow.service.audit_logger.event_repository.session is c.session
    assert workflow.service.now_provider() == c.now
