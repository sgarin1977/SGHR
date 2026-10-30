"""Real local Suite/Core provisioning inside the bootstrap transaction.

SQLite proves local transaction behavior, not native privileges or PG locks.
"""
import json
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from database.models import (
    Module, Suite, SuiteModule, TenantLanguage, TenantModule, TenantSuite,
)
from database.repositories.white_label import WhiteLabelRepository
from services.platform_construction_onboarding import (
    PlatformConstructionOnboarding, build_platform_construction_onboarding,
)
from services.platform_organization_bootstrap import (
    OrganizationBootstrapError, build_platform_organization_bootstrap,
)
from services.platform_organization_creation_policy import (
    ReleaseOrganizationCreationPolicy,
)
from test_platform_organization_bootstrap import case, run, counts
from test_platform_organization_creation_policy import release_case


@pytest.fixture
def onboarding_case(release_case):
    c = release_case
    for model in (
        Suite, Module, SuiteModule, TenantSuite, TenantModule, TenantLanguage,
    ):
        model.__table__.create(c.sync.get_bind())

    c.catalogue_suite = Suite(
        id=uuid4(), code="construction", name="Construction",
        status="active", created_at=c.now, updated_at=c.now,
    )
    c.catalogue_core = Module(
        id=uuid4(), code="construction.core", name="Construction Core",
        status="active", created_at=c.now, updated_at=c.now,
    )
    c.other_module = Module(
        id=uuid4(), code="construction.future", name="Future module",
        status="active", created_at=c.now, updated_at=c.now,
    )
    c.sync.add_all([c.catalogue_suite, c.catalogue_core, c.other_module])
    c.sync.flush()
    c.sync.add(SuiteModule(
        suite_id=c.catalogue_suite.id, module_id=c.catalogue_core.id,
        is_required=True, created_at=c.now,
    ))
    c.sync.commit()

    c.onboarding = build_platform_construction_onboarding(session=c.session)
    c.workflow.policy = ReleaseOrganizationCreationPolicy(
        repository=c.repo, provisioner=c.onboarding,
    )
    c.workflow.modules = WhiteLabelRepository(c.session)
    return c


def assignment_counts(c):
    return tuple(c.sync.execute(text(
        "SELECT count(*) FROM " + table
    )).scalar_one() for table in (
        "tenant_suites", "tenant_modules", "tenant_languages",
    ))


@pytest.mark.asyncio
async def test_bootstrap_provisions_existing_suite_core_and_default_language(onboarding_case):
    c = onboarding_case
    response = await run(c)
    tenant_id = UUID(response["id"])
    assert counts(c) == (1, 1, 1, 1, 1, 0)
    assert assignment_counts(c) == (1, 1, 1)

    suite = c.sync.execute(select(TenantSuite)).scalar_one()
    module = c.sync.execute(select(TenantModule)).scalar_one()
    language = c.sync.execute(select(TenantLanguage)).scalar_one()
    assert suite.tenant_id == module.tenant_id == language.tenant_id == tenant_id
    assert suite.suite_id == c.catalogue_suite.id
    assert suite.status == "active" and suite.expires_at is None
    assert module.module_id == c.catalogue_core.id
    assert module.is_enabled is True and module.source == "suite"
    assert language.language_code == "uk" and language.is_active is True
    assert c.sync.execute(select(TenantModule).where(
        TenantModule.module_id == c.other_module.id,
    )).scalar_one_or_none() is None
    audit = json.loads(c.sync.execute(text(
        "SELECT payload FROM probe_audit"
    )).scalar_one())
    assert audit["payload"]["suite_code"] == "construction"
    assert audit["payload"]["module_codes"] == ["construction.core"]
    assert c.session.commits == 1


@pytest.mark.asyncio
async def test_successful_replay_does_not_reactivate_later_disabled_access(onboarding_case):
    c = onboarding_case
    original = await run(c)
    c.sync.execute(text("UPDATE tenant_suites SET status='disabled'"))
    c.sync.execute(text("UPDATE tenant_modules SET is_enabled=0"))
    c.sync.commit()
    assert await run(c) == original
    assert assignment_counts(c) == (1, 1, 1)
    assert c.sync.execute(text(
        "SELECT status FROM tenant_suites"
    )).scalar_one() == "disabled"
    assert c.sync.execute(text(
        "SELECT is_enabled FROM tenant_modules"
    )).scalar_one() == 0
    assert counts(c) == (1, 1, 1, 1, 1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["suite", "core", "mapping"])
async def test_unavailable_catalogue_rolls_back_whole_bootstrap(onboarding_case, failure):
    c = onboarding_case
    if failure == "suite":
        c.sync.execute(text("UPDATE suites SET status='disabled'"))
    elif failure == "core":
        c.sync.execute(text(
            "UPDATE modules SET status='disabled' WHERE code='construction.core'"
        ))
    else:
        c.sync.execute(text("DELETE FROM suite_modules"))
    c.sync.commit()
    with pytest.raises(OrganizationBootstrapError):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    assert assignment_counts(c) == (0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit", "complete", "commit"])
async def test_flushed_suite_core_rows_roll_back_with_owner_and_membership(onboarding_case, failure):
    c = onboarding_case
    if failure == "commit":
        c.session.fail_commit = True
    else:
        target, method = (
            (c.events, "create_event") if failure == "audit"
            else (c.idempotency, "complete")
        )
        original = getattr(target, method)
        async def fail_after_write(**kwargs):
            await original(**kwargs)
            raise RuntimeError("local injected failure")
        setattr(target, method, fail_after_write)
    with pytest.raises(OrganizationBootstrapError):
        await run(c)
    assert counts(c) == (0, 0, 0, 0, 0, 0)
    assert assignment_counts(c) == (0, 0, 0)
    assert c.session.commits == 0 and c.session.rollbacks == 1


@pytest.mark.asyncio
async def test_second_organization_gets_independent_assignments(onboarding_case):
    c = onboarding_case
    first = await run(c)
    second = await run(c, idempotency_key="second-bootstrap")
    assert first["id"] != second["id"]
    assert assignment_counts(c) == (2, 2, 2)
    assert counts(c) == (2, 2, 2, 2, 2, 0)
    assert c.sync.execute(text("SELECT count(*) FROM suites")).scalar_one() == 1
    assert c.sync.execute(text("SELECT count(*) FROM modules")).scalar_one() == 2


def test_runtime_factory_has_canonical_default_provisioner(onboarding_case):
    c = onboarding_case
    workflow = build_platform_organization_bootstrap(
        session=c.session,
        encryption_key=Fernet.generate_key().decode("ascii"),
    )
    assert isinstance(workflow.policy, ReleaseOrganizationCreationPolicy)
    assert isinstance(workflow.policy.provisioner, PlatformConstructionOnboarding)
    assert workflow.policy.provisioner.session is c.session
