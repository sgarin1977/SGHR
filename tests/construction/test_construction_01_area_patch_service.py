import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError

from database.repositories.construction import ConstructionProjectAreaRepository
from services.construction.construction_areas import (
    ConstructionProjectAreaValidationError, ConstructionProjectAreaOperationError,
    ConstructionProjectAreaNotFoundError, ConstructionProjectAreaProjectNotFoundError,
)
from services.construction.construction_foundation import ConstructionProjectTerminalError, ConstructionRowVersionConflictError
from services.construction.construction_permissions import (
    ConstructionAccessContext, ConstructionGrantContext, ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from test_construction_01_project_create_transaction import create_case, real_create
from test_construction_01_area_archive_transaction import archive_case, real_area


@pytest.fixture
def patch_case(archive_case):
    c = archive_case
    c.area.name = "Original room"
    c.area.sort_order = 4
    c.area.area_type = "ROOM"
    c.area.status = "confirmed"
    c.calls = []
    async def project_lock(**kwargs):
        c.calls.append("project")
        return SimpleNamespace(id=c.project_id, tenant_id=c.actor.tenant_id, status="active")
    async def area_lock(**kwargs):
        c.calls.append("area")
        return c.area
    async def update(**kwargs):
        c.calls.append("update")
        for field, value in kwargs["fields"].items():
            setattr(c.area, field, value)
        c.area.row_version += 1
        return c.area
    c.projects.get_active_project_for_update.side_effect = project_lock
    c.repo.get_active_area_for_update.side_effect = area_lock
    c.repo.update_area_fields = AsyncMock(side_effect=update)
    return c


async def patch(c, fields, **options):
    arguments = dict(
        actor=c.actor, access_context=c.context, project_id=c.project_id,
        area_id=c.area.id, expected_row_version=7, fields=fields, trace_id="area-patch-test",
    )
    arguments.update(options)
    return await c.service.patch_area(**arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, True, False])
@pytest.mark.parametrize("fields", [
    {"name": "  New room  "}, {"sort_order": None},
    {"sort_order": 8, "name": "New room"},
])
async def test_patch_changes_confirmed_area_once_with_exact_audit(patch_case, fields, mode):
    c = patch_case
    original = dict(name=c.area.name, sort_order=c.area.sort_order)
    incoming = dict(fields)
    normalized = {key: value.strip() if key == "name" else value for key, value in fields.items()}
    options = {} if mode is None else {"manage_transaction": mode}
    assert await patch(c, fields, **options) is c.area
    assert fields == incoming
    assert c.calls == ["project", "area", "update"]
    c.projects.get_active_project_for_update.assert_awaited_once_with(
        tenant_id=c.actor.tenant_id, project_id=c.project_id,
    )
    c.repo.get_active_area_for_update.assert_awaited_once_with(
        tenant_id=c.actor.tenant_id, project_id=c.project_id, area_id=c.area.id,
    )
    c.repo.update_area_fields.assert_awaited_once_with(area=c.area, fields=normalized)
    assert c.area.row_version == 8 and c.area.status == "confirmed" and c.area.area_type == "ROOM"
    assert c.area.deleted_at is None
    c.events.create_event.assert_awaited_once()
    event = c.events.create_event.await_args.kwargs
    assert event["event_type"] == "construction_project_area_updated"
    assert event["entity_id"] == c.area.id and event["tenant_id"] == c.actor.tenant_id
    assert event["user_id"] == c.actor.user_id and event["trace_id"] == "area-patch-test"
    assert event["payload"]["operation"] == "update"
    assert event["payload"]["changes"] == [
        {"field": key, "old_value": original[key], "new_value": normalized[key]}
        for key in ("name", "sort_order") if key in normalized
    ]
    assert c.session.commit.await_count == (0 if mode is False else 1)
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{}, {"name": " Original room "}, {"name": "Original room", "sort_order": 4}])
async def test_no_op_checks_version_but_changes_neither_audit_nor_version(patch_case, fields):
    c = patch_case
    assert await patch(c, fields) is c.area
    assert c.calls == ["project", "area"] and c.area.row_version == 7
    c.repo.update_area_fields.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_contains_only_actually_changed_fields(patch_case):
    c = patch_case
    await patch(c, {"name": " Original room ", "sort_order": None})
    assert c.repo.update_area_fields.await_args.kwargs["fields"] == {"sort_order": None}
    assert c.events.create_event.await_args.kwargs["payload"]["changes"] == [
        {"field": "sort_order", "old_value": 4, "new_value": None},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    None, [], {"name": None}, {"name": "  "}, {"name": 3},
    {"sort_order": True}, {"sort_order": "4"}, {"sort_order": 4.0},
    {"project_id": uuid4()}, {"tenant_id": uuid4()}, {"status": "draft"}, {"area_type": "OTHER"},
])
async def test_invalid_or_unapproved_fields_fail_before_locks(patch_case, fields):
    c = patch_case
    with pytest.raises(ConstructionProjectAreaValidationError):
        await patch(c, fields)
    assert not c.calls
    c.repo.update_area_fields.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [True, "7", 0, None])
async def test_invalid_expected_version_fails_before_locks(patch_case, version):
    c = patch_case
    with pytest.raises(ConstructionProjectAreaValidationError):
        await patch(c, {}, expected_row_version=version)
    assert not c.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "scope"])
async def test_patch_does_not_bypass_common_access_policy(patch_case, failure):
    c = patch_case
    if failure == "permission":
        c.context = ConstructionAccessContext(c.actor.user_id, c.actor.tenant_id, ("OWNER",), (), c.context.grants)
        expected = ConstructionPermissionDeniedError
    else:
        c.context = ConstructionAccessContext(
            c.actor.user_id, c.actor.tenant_id, ("PROJECT_MANAGER",), ("construction.projects.edit",),
            (ConstructionGrantContext("PROJECT_MANAGER", "project", c.project_id),),
        )
        expected = ConstructionScopeDeniedError
    with pytest.raises(expected):
        await patch(c, {"name": "New"}, **({"project_id": uuid4()} if failure == "scope" else {}))
    assert not c.calls
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [True, False])
@pytest.mark.parametrize("failure", ["stale", "repository", "audit", "orm_stale"])
async def test_failures_preserve_typed_errors_and_transaction_owner(patch_case, mode, failure):
    c = patch_case
    expected = ConstructionProjectAreaOperationError
    if failure == "stale":
        c.area.row_version = 8
        expected = ConstructionRowVersionConflictError
    elif failure == "repository":
        c.repo.update_area_fields.side_effect = RuntimeError("private-update-error")
    elif failure == "audit":
        c.events.create_event.side_effect = RuntimeError("private-audit-error")
    else:
        c.repo.update_area_fields.side_effect = StaleDataError("private-version-error")
        expected = ConstructionRowVersionConflictError
    with pytest.raises(expected) as caught:
        await patch(c, {"name": "New"}, manage_transaction=mode)
    assert "private-" not in str(caught.value)
    c.session.commit.assert_not_called()
    assert c.session.rollback.await_count == (1 if mode else 0)
    if failure in {"stale", "repository", "orm_stale"}:
        c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "cancelled"])
async def test_terminal_project_denies_patch_even_if_no_op(patch_case, status):
    c = patch_case
    c.projects.get_active_project_for_update.side_effect = None
    c.projects.get_active_project_for_update.return_value.status = status
    with pytest.raises(ConstructionProjectTerminalError):
        await patch(c, {})
    c.repo.get_active_area_for_update.assert_not_called()
    c.repo.update_area_fields.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["project", "area"])
async def test_missing_or_archived_resources_cannot_be_patched(patch_case, missing):
    c = patch_case
    target = c.projects.get_active_project_for_update if missing == "project" else c.repo.get_active_area_for_update
    target.side_effect = None
    target.return_value = None
    expected = ConstructionProjectAreaProjectNotFoundError if missing == "project" else ConstructionProjectAreaNotFoundError
    with pytest.raises(expected):
        await patch(c, {"name": "New"})
    c.repo.update_area_fields.assert_not_called()
    c.events.create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["id", "tenant_id", "project_id"])
async def test_unexpected_locked_entity_is_rejected_before_mutation(patch_case, field):
    c = patch_case
    identifier = c.area.id
    setattr(c.area, field, uuid4())
    with pytest.raises(ConstructionProjectAreaOperationError):
        await patch(c, {"name": "New"}, area_id=identifier)
    c.repo.update_area_fields.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [0, None, "false"])
async def test_invalid_transaction_flag_is_rejected(patch_case, mode):
    c = patch_case
    with pytest.raises(ConstructionProjectAreaValidationError):
        await patch(c, {"name": "New"}, manage_transaction=mode)
    assert not c.calls


@pytest.mark.asyncio
async def test_borrowed_mode_requires_transaction_and_no_op_never_commits(patch_case):
    c = patch_case
    c.session.in_transaction = lambda: False
    with pytest.raises(ConstructionProjectAreaOperationError):
        await patch(c, {}, manage_transaction=False)
    assert not c.calls
    c.session.in_transaction = lambda: True
    await patch(c, {}, manage_transaction=False)
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_owned_commit_failure_rolls_back(patch_case):
    c = patch_case
    c.session.commit.side_effect = RuntimeError("private-commit-error")
    with pytest.raises(ConstructionProjectAreaOperationError):
        await patch(c, {"name": "New"})
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_repository_rejects_immutable_fields_and_empty_update_does_not_flush():
    session = SimpleNamespace(flush=AsyncMock())
    repo = ConstructionProjectAreaRepository(session)
    area = SimpleNamespace(name="Room", project_id=uuid4())
    with pytest.raises(ValueError):
        await repo.update_area_fields(area=area, fields={"project_id": uuid4()})
    assert await repo.update_area_fields(area=area, fields={}) is area
    session.flush.assert_not_called()


async def real_patch(c, fields):
    return await c.area_service.patch_area(
        actor=c.actor, access_context=c.context, project_id=c.project_id,
        area_id=c.area_id, expected_row_version=1, fields=fields,
        trace_id="real-area-patch", manage_transaction=False,
    )


def persisted(c):
    return c.sql_session.execute(text(
        "SELECT name, sort_order, status, row_version, deleted_at "
        "FROM construction_project_areas WHERE id=:id"
    ), {"id": c.area_id.hex}).one()


@pytest.mark.asyncio
async def test_real_two_field_update_has_one_version_and_one_audit(real_area):
    c = real_area
    with c.sql_session.begin():
        area = await real_patch(c, {"name": " Updated room ", "sort_order": 8})
        assert area.row_version == 2
    state = persisted(c)
    assert state.name == "Updated room" and state.sort_order == 8 and state.row_version == 2
    assert state.status == "draft" and state.deleted_at is None
    rows = c.sql_session.execute(text("SELECT event_type,payload FROM tx_test_audit")).all()
    updates = [json.loads(r.payload) for r in rows if r.event_type == "construction_project_area_updated"]
    assert len(updates) == 1
    assert updates[0]["changes"] == [
        {"field": "name", "old_value": "Archive room", "new_value": "Updated room"},
        {"field": "sort_order", "old_value": None, "new_value": 8},
    ]
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "later_workflow"])
async def test_real_patch_and_audit_roll_back_together(real_area, failure):
    c = real_area
    c.events.fail_after_insert = failure == "audit"
    expected = ConstructionProjectAreaOperationError if failure == "audit" else RuntimeError
    with pytest.raises(expected):
        with c.sql_session.begin():
            await real_patch(c, {"name": "New", "sort_order": 8})
            assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 2
            raise RuntimeError("forced-later-failure")
    state = persisted(c)
    assert state.name == "Archive room" and state.sort_order is None and state.row_version == 1
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 1


@pytest.mark.asyncio
async def test_real_no_op_leaves_persisted_version_and_audit_unchanged(real_area):
    c = real_area
    with c.sql_session.begin():
        area = await real_patch(c, {"name": " Archive room ", "sort_order": None})
        assert area.row_version == 1
    state = persisted(c)
    assert state.name == "Archive room" and state.row_version == 1
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 1


@pytest.mark.asyncio
async def test_real_version_predicate_rejects_intervening_update(real_area):
    c = real_area
    original = c.areas.update_area_fields
    async def intervening_update(**kwargs):
        c.sql_session.execute(text(
            "UPDATE construction_project_areas SET row_version=2 WHERE id=:id"
        ), {"id": c.area_id.hex})
        return await original(**kwargs)
    c.areas.update_area_fields = intervening_update
    with pytest.raises(ConstructionRowVersionConflictError) as caught:
        with c.sql_session.begin():
            await real_patch(c, {"name": "New", "sort_order": 8})
    assert caught.value.code == "ROW_VERSION_CONFLICT"
    state = persisted(c)
    assert state.name == "Archive room" and state.sort_order is None and state.row_version == 1
    assert c.sql_session.execute(text("SELECT count(*) FROM tx_test_audit")).scalar_one() == 1
