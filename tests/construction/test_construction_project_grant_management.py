"""Scoped team management through existing grant HTTP/workflow boundaries."""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from services.construction.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext,
    ConstructionGrantContext, ConstructionPermissionDeniedError,
    ConstructionScopeDeniedError, require_construction_grant_management_scope,
)
from test_construction_1a_access_mutation_workflow import pipeline, snapshot
from test_construction_1a_access_mutation_http import http_case, post as mutate_post
from test_construction_1a_access_issuance_workflow import issuance_pipeline
from test_construction_1a_access_issuance_http import issuance_http, post as issue_post
from test_construction_1a_access_mutation_workflow import pipeline as grant_pipeline
from test_construction_1a_membership_writer_integration import (
    writer_case, issue as serialized_issue, mutate as serialized_mutate,
    expected_pair,
)


def authority(c, *, role="PROJECT_MANAGER", scope=None, additional=()):
    scope = scope or c.project_id
    c.current_authority = [
        NS(role=role, scope_type="project", scope_id=scope, status="active", expires_at=None),
        *additional,
    ]
    roles = tuple(sorted({item.role for item in c.current_authority}))
    permissions = tuple(sorted(set().union(
        *(CONSTRUCTION_ROLE_PERMISSIONS[item] for item in roles)
    )))
    c.http_access = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, roles, permissions,
        tuple(ConstructionGrantContext(item.role, item.scope_type, item.scope_id)
              for item in c.current_authority),
    )
    async def current(**kwargs):
        return list(c.current_authority) if c.authorized else []
    c.repo.list_active_grants.side_effect = current


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "pm-issue"])
async def test_project_manager_can_issue_within_own_project(issuance_http, key):
    c = issuance_http
    authority(c)
    assert "construction.access.manage" not in c.http_access.permissions
    response = await issue_post(c, key=key)
    assert response.status_code == 201, response.text
    assert response.json()["data"]["scope_id"] == str(c.project_id)
    assert len(c.rows("grant")) == 2 and len(c.rows("audit")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replace", "revoke"])
@pytest.mark.parametrize("key", [None, "pm-mutation"])
async def test_project_manager_can_replace_and_revoke_delegable_roles(http_case, operation, key):
    c = http_case
    authority(c)
    response = await mutate_post(c, operation, key=key)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["scope_id"] == str(c.project_id)
    assert len(c.rows("audit")) == (2 if operation == "replace" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "foreign_project", "OWNER", "ADMIN"])
async def test_issue_cannot_cross_scope_or_role_ceiling(issuance_http, failure):
    c = issuance_http
    authority(c)
    before = snapshot(c)
    if failure == "tenant":
        changes = {"role": "ADMIN", "scope_type": "tenant", "scope_id": str(c.actor.tenant_id)}
    elif failure == "foreign_project":
        changes = {"scope_id": str(uuid4())}
    else:
        changes = {"role": failure}
    response = await issue_post(c, changes=changes, key="forbidden-issue")
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.repo.create_grant.assert_not_awaited()
    c.session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replace", "revoke"])
@pytest.mark.parametrize("original", ["tenant", "other_project", "OWNER", "ADMIN"])
async def test_original_grant_must_also_fit_scope_and_ceiling(http_case, operation, original):
    c = http_case
    authority(c)
    if original == "tenant":
        c.original.scope_type = "tenant"
        c.original.scope_id = c.actor.tenant_id
        c.original.role = "ADMIN"
    elif original == "other_project":
        c.original.scope_id = uuid4()
    else:
        c.original.role = original
    c.save("grant", c.original.id, c.original)
    c.db.commit()
    before = snapshot(c)
    response = await mutate_post(c, operation, key="forbidden-original")
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.repo.revoke_if_effective.assert_not_awaited()
    c.repo.create_grant.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_cannot_move_access_into_another_project(http_case):
    c = http_case
    authority(c)
    other = uuid4()
    c.service.project_repository.get_active_project_for_update.return_value = NS(
        id=other, tenant_id=c.actor.tenant_id, deleted_at=None,
    )
    before = snapshot(c)
    response = await mutate_post(c, "replace", changes={"scope_id": str(other)})
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_owner_in_other_project_does_not_raise_own_project_ceiling(issuance_http):
    c = issuance_http
    authority(c, additional=[NS(
        role="OWNER", scope_type="project", scope_id=uuid4(),
        status="active", expires_at=None,
    )])
    before = snapshot(c)
    response = await issue_post(c, changes={"role": "OWNER"}, key="cross-project-owner")
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_replacement_rechecks_original_role_after_membership_wait(http_case):
    c = http_case
    authority(c, role="OWNER")
    c.original.role = "OWNER"
    c.save("grant", c.original.id, c.original)
    c.db.commit()
    before = snapshot(c)
    async def membership(**kwargs):
        c.current_authority = [NS(
            role="PROJECT_MANAGER", scope_type="project", scope_id=c.project_id,
            status="active", expires_at=None,
        )]
        return True
    c.repo.has_active_platform_membership.side_effect = membership
    response = await mutate_post(c, "replace", key="downgraded-original")
    assert response.status_code == 403, response.text
    assert snapshot(c) == before
    c.repo.revoke_if_effective.assert_not_awaited()
    c.repo.create_grant.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["replace", "revoke"])
async def test_keyed_replay_still_requires_fresh_project_authority(http_case, operation):
    c = http_case
    authority(c)
    first = await mutate_post(c, operation, key="pm-replay")
    assert first.status_code == 200, first.text
    before = snapshot(c)
    c.authorized = False
    denied = await mutate_post(c, operation, key="pm-replay")
    assert denied.status_code == 403, denied.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_target_membership_is_not_created_by_project_issuance(issuance_http):
    c = issuance_http
    authority(c)
    c.repo.has_active_platform_membership.return_value = False
    before = snapshot(c)
    response = await issue_post(c, key="nonmember")
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
async def test_platform_or_jwt_permission_claims_cannot_replace_construction_grants(http_case):
    c = http_case
    c.actor.permissions = ("construction.team.manage", "construction.access.manage")
    c.actor.roles = ("super_admin",)
    c.http_access = ConstructionAccessContext(
        c.actor.user_id, c.actor.tenant_id, (), (), (),
    )
    before = snapshot(c)
    response = await mutate_post(c, "revoke")
    assert response.status_code == 403, response.text
    assert snapshot(c) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["issue", "replace", "revoke"])
async def test_serialized_writers_preserve_membership_barriers(writer_case, operation):
    c = writer_case
    authority(c)
    if operation == "issue":
        await serialized_issue(c, key="serialized-project-team")
    else:
        await serialized_mutate(
            c, revoke=operation == "revoke", key="serialized-project-team",
        )
    assert c.barriers[:2] == expected_pair(c)
    assert c.session.commit.await_count == 1


def test_project_team_policy_does_not_authorize_tenant_or_document_operations(http_case):
    c = http_case
    authority(c)
    for kind, scope in (
        ("tenant", c.actor.tenant_id), ("document", uuid4()), ("project", uuid4()),
    ):
        with pytest.raises((ConstructionPermissionDeniedError, ConstructionScopeDeniedError)):
            require_construction_grant_management_scope(
                actor=c.actor, access_context=c.http_access,
                scope_type=kind, scope_id=scope,
            )
