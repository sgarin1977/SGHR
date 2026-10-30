"""Release eligibility integrated with the existing bootstrap transaction."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from services.api_platform_principal import ApiPlatformPrincipal
from services.platform_organization_bootstrap import (
    OrganizationBootstrapDenied,
    build_platform_organization_bootstrap,
)
from services.platform_organization_creation_policy import (
    ReleaseOrganizationCreationPolicy,
)
from test_platform_organization_bootstrap import case, run, counts


@pytest.fixture
def release_case(case):
    c = case
    c.sync.execute(text(
        """CREATE TABLE user_accounts (
            id CHAR(32) PRIMARY KEY, user_id CHAR(32) NOT NULL,
            platform TEXT NOT NULL, platform_user_id TEXT NOT NULL,
            status TEXT NOT NULL
        )"""
    ))
    c.account_id = uuid4()
    c.sync.execute(text(
        """INSERT INTO user_accounts
           (id,user_id,platform,platform_user_id,status)
           VALUES (:id,:user,'telegram','123','active')"""
    ), {"id": c.account_id.hex, "user": c.user.hex})
    c.sync.commit()
    c.provisioner = c.policy
    c.workflow.policy = ReleaseOrganizationCreationPolicy(
        repository=c.repo, provisioner=c.provisioner,
    )
    return c


@pytest.mark.asyncio
async def test_verified_active_creator_needs_no_existing_organization_or_roles(release_case):
    c = release_case
    c.sync.execute(text("UPDATE users SET tenant_id=NULL"))
    c.sync.commit()
    response = await run(c)
    assert counts(c) == (1, 1, 1, 1, 1, 1)
    assert response["creator_user_id"] == str(c.user)
    assert c.sync.execute(text(
        "SELECT tenant_id FROM users"
    )).scalar_one() is None
    assert c.sync.execute(text(
        "SELECT role FROM user_roles"
    )).scalar_one() == "client"
    assert c.sync.execute(text(
        "SELECT role FROM construction_access_grants"
    )).scalar_one() == "OWNER"
    c.provisioner.require_creation.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_organizations_are_allowed(release_case):
    c = release_case
    first = await run(c)
    second = await run(c, idempotency_key="another-organization")
    assert first["id"] != second["id"]
    assert counts(c) == (2, 2, 2, 2, 2, 2)
    assert c.sync.execute(text(
        "SELECT count(DISTINCT tenant_id) FROM construction_access_grants "
        "WHERE role='OWNER'"
    )).scalar_one() == 2
    c.provisioner.require_creation.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_product_eligibility_cannot_add_release_prerequisites(release_case):
    c = release_case
    c.provisioner.require_creation.return_value = False
    response = await run(c)
    UUID(response["id"])
    assert counts(c) == (1, 1, 1, 1, 1, 1)
    c.provisioner.require_creation.assert_not_awaited()
    c.provisioner.provision_construction.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["blocked", "suspended", "revoked", "deleted"])
async def test_nonactive_platform_user_is_denied(release_case, status):
    c = release_case
    c.sync.execute(text(
        "UPDATE users SET status=:status"
    ), {"status": status})
    c.sync.commit()
    with pytest.raises(OrganizationBootstrapDenied):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    c.provisioner.provision_construction.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "unlinked", "empty_identity", "foreign"])
async def test_absent_current_canonical_identity_is_denied(release_case, failure):
    c = release_case
    if failure == "missing":
        c.sync.execute(text("DELETE FROM user_accounts"))
    elif failure == "unlinked":
        c.sync.execute(text("UPDATE user_accounts SET status='unlinked'"))
    elif failure == "empty_identity":
        c.sync.execute(text("UPDATE user_accounts SET platform_user_id=' '"))
    else:
        c.sync.execute(text(
            "UPDATE user_accounts SET user_id=:other"
        ), {"other": uuid4().hex})
    c.sync.commit()
    with pytest.raises(OrganizationBootstrapDenied):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)


@pytest.mark.asyncio
async def test_unlinked_account_does_not_mask_another_active_identity(release_case):
    c = release_case
    c.sync.execute(text(
        """INSERT INTO user_accounts
           (id,user_id,platform,platform_user_id,status)
           VALUES (:id,:user,'telegram','456','unlinked')"""
    ), {"id": uuid4().hex, "user": c.user.hex})
    c.sync.commit()
    await run(c)
    assert counts(c) == (1, 1, 1, 1, 1, 1)


@pytest.mark.asyncio
async def test_current_identity_is_rechecked_before_bootstrap_writes(release_case):
    c = release_case
    c.repo.has_active_creation_identity = AsyncMock(side_effect=[True, False])
    with pytest.raises(OrganizationBootstrapDenied):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    c.events.create_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_does_not_create_second_organization_or_restore_access(release_case):
    c = release_case
    first = await run(c)
    c.sync.execute(text("UPDATE user_roles SET status='revoked'"))
    c.sync.execute(text("UPDATE construction_access_grants SET status='revoked'"))
    c.sync.execute(text("DELETE FROM probe_product"))
    c.sync.commit()
    assert await run(c) == first
    assert counts(c) == (1, 1, 1, 1, 1, 0)
    assert c.sync.execute(text(
        "SELECT status FROM construction_access_grants"
    )).scalar_one() == "revoked"
    c.provisioner.provision_construction.assert_awaited_once()


@pytest.mark.asyncio
async def test_principal_is_required_even_if_database_account_exists(release_case):
    c = release_case
    with pytest.raises(OrganizationBootstrapDenied):
        await run(c, principal=NS(user_id=c.user))
    assert counts(c) == (0, 0, 0, 0, 0, 0)


def test_runtime_factory_uses_release_policy(release_case):
    c = release_case
    workflow = build_platform_organization_bootstrap(
        session=c.session, product_policy=c.provisioner,
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    assert isinstance(workflow.policy, ReleaseOrganizationCreationPolicy)
    assert workflow.policy.provisioner is c.provisioner
    assert workflow.policy.repository is workflow.repository


def test_foreign_transaction_provisioner_is_rejected(release_case):
    c = release_case
    with pytest.raises(ValueError):
        ReleaseOrganizationCreationPolicy(
            repository=c.repo,
            provisioner=NS(
                session=object(), provision_construction=AsyncMock(),
            ),
        )
