from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_bank_account_create_uses_actor_tenant_and_audits_before_commit():
    from services.platform_company_bank_accounts import (
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=uuid4(),
        tenant_company_profile_id=profile.id,
    )
    calls = []

    profile_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=profile),
    )
    account_repository = SimpleNamespace(
        create_account=AsyncMock(return_value=account),
    )

    async def record_event(**kwargs):
        calls.append(("audit", kwargs))

    async def commit():
        calls.append(("commit",))

    session = SimpleNamespace(
        commit=AsyncMock(side_effect=commit),
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=profile_repository,
        account_repository=account_repository,
        event_repository=SimpleNamespace(create_event=record_event),
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=user_id,
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    result = await service.create_account(
        actor=actor,
        account_holder_name="Example Legal Ltd",
        country_code="PT",
        iban="PT50 0002 0123 1234 5678 9015 4",
        bic_swift=" cgdiptpl ",
        account_number=None,
        currency=" eur ",
    )

    assert result is account
    profile_repository.get_for_tenant_for_update.assert_awaited_once_with(
        tenant_id=tenant_id,
    )
    fields = account_repository.create_account.await_args.kwargs
    assert fields["profile"] is profile
    assert fields["iban"] == "PT50000201231234567890154"
    assert fields["bic_swift"] == "CGDIPTPL"
    assert fields["currency"] == "EUR"

    assert [call[0] for call in calls] == ["audit", "commit"]
    event = calls[0][1]
    assert event["tenant_id"] == tenant_id
    assert event["user_id"] == user_id
    assert event["entity_id"] == account.id
    assert "PT50000201231234567890154" not in str(event["payload"])
    session.rollback.assert_not_awaited()

@pytest.mark.asyncio
async def test_bank_account_create_rolls_back_when_audit_fails():
    from services.platform_company_bank_accounts import (
        CompanyBankAccountOperationError,
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=uuid4(),
        tenant_company_profile_id=profile.id,
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    event_repository = SimpleNamespace(
        create_event=AsyncMock(
            side_effect=RuntimeError("Private database details")
        ),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(
                return_value=profile
            ),
        ),
        account_repository=SimpleNamespace(
            create_account=AsyncMock(return_value=account),
        ),
        event_repository=event_repository,
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    with pytest.raises(CompanyBankAccountOperationError) as error:
        await service.create_account(
            actor=actor,
            account_holder_name="Example Legal Ltd",
            currency="EUR",
            iban="PT50000201231234567890154",
        )

    event_repository.create_event.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    assert "Private database details" not in str(error.value)

@pytest.mark.asyncio
async def test_bank_account_create_requires_manage_permission():
    from services.platform_company_bank_accounts import (
        TenantCompanyBankAccountService,
    )
    from services.platform_company_profile_access import (
        CompanyProfilePermissionDeniedError,
    )

    profile_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(),
    )
    account_repository = SimpleNamespace(
        create_account=AsyncMock(),
    )
    event_repository = SimpleNamespace(
        create_event=AsyncMock(),
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=profile_repository,
        account_repository=account_repository,
        event_repository=event_repository,
    )
    actor = SimpleNamespace(
        tenant_id=uuid4(),
        user_id=uuid4(),
        status="active",
        permissions=(),
    )

    with pytest.raises(CompanyProfilePermissionDeniedError):
        await service.create_account(
            actor=actor,
            account_holder_name="Example Legal Ltd",
            currency="EUR",
            iban="PT50000201231234567890154",
        )

    profile_repository.get_for_tenant_for_update.assert_not_awaited()
    account_repository.create_account.assert_not_awaited()
    event_repository.create_event.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bank_account_create_rejects_foreign_profile():
    from services.platform_company_bank_accounts import (
        CompanyBankAccountOperationError,
        TenantCompanyBankAccountService,
    )

    actor = SimpleNamespace(
        tenant_id=uuid4(),
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    profile_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                tenant_id=uuid4(),
            ),
        ),
    )
    account_repository = SimpleNamespace(
        create_account=AsyncMock(),
    )
    event_repository = SimpleNamespace(
        create_event=AsyncMock(),
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=profile_repository,
        account_repository=account_repository,
        event_repository=event_repository,
    )

    with pytest.raises(CompanyBankAccountOperationError):
        await service.create_account(
            actor=actor,
            account_holder_name="Example Legal Ltd",
            currency="EUR",
            iban="PT50000201231234567890154",
        )

    profile_repository.get_for_tenant_for_update.assert_awaited_once_with(
        tenant_id=actor.tenant_id,
    )
    account_repository.create_account.assert_not_awaited()
    event_repository.create_event.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_bank_account_lock_is_tenant_scoped_and_refreshes_state():
    from sqlalchemy.dialects import postgresql

    from database.repositories.company_profiles import (
        TenantCompanyBankAccountRepository,
    )

    tenant_id = uuid4()
    account_id = uuid4()
    account = SimpleNamespace(id=account_id)
    statements = []

    class Result:
        def scalar_one_or_none(self):
            return account

    class Session:
        async def execute(self, statement):
            statements.append(statement)
            return Result()

    result = await TenantCompanyBankAccountRepository(
        Session()
    ).get_for_tenant_for_update(
        tenant_id=tenant_id,
        account_id=account_id,
    )

    assert result is account
    assert len(statements) == 1
    statement = statements[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()

    assert "join tenant_company_profiles" in sql
    assert "tenant_company_profiles.tenant_id" in sql
    assert "tenant_company_bank_accounts.id" in sql
    assert "for update of tenant_company_bank_accounts" in sql
    assert statement.get_execution_options()["populate_existing"] is True
    assert tenant_id in compiled.params.values()
    assert account_id in compiled.params.values()

@pytest.mark.asyncio
async def test_bank_account_update_rejects_stale_row_version():
    from services.platform_company_bank_accounts import (
        CompanyBankAccountRowVersionConflictError,
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    account_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=account_id,
        tenant_company_profile_id=profile.id,
        row_version=2,
        bank_name="Old Bank",
    )
    account_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=account),
        update_account=AsyncMock(),
    )
    event_repository = SimpleNamespace(create_event=AsyncMock())
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(return_value=profile),
        ),
        account_repository=account_repository,
        event_repository=event_repository,
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    with pytest.raises(
        CompanyBankAccountRowVersionConflictError
    ) as error:
        await service.update_account(
            actor=actor,
            account_id=account_id,
            expected_row_version=1,
            changes={"bank_name": "New Bank"},
        )

    assert error.value.status_code == 409
    assert error.value.code == "ROW_VERSION_CONFLICT"
    account_repository.get_for_tenant_for_update.assert_awaited_once_with(
        tenant_id=tenant_id,
        account_id=account_id,
    )
    account_repository.update_account.assert_not_awaited()
    event_repository.create_event.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_bank_account_repository_updates_without_commit():
    from database.models import TenantCompanyBankAccount
    from database.repositories.company_profiles import (
        TenantCompanyBankAccountRepository,
    )

    session = SimpleNamespace(
        flush=AsyncMock(),
        commit=AsyncMock(),
    )
    account = TenantCompanyBankAccount(
        id=uuid4(),
        tenant_company_profile_id=uuid4(),
        account_holder_name="Example Legal Ltd",
        currency="EUR",
        iban="PT50000201231234567890154",
        account_number=None,
        is_active=True,
        show_in_documents=False,
    )

    result = await TenantCompanyBankAccountRepository(
        session
    ).update_account(
        account=account,
        values={
            "bank_name": "Example Bank",
            "show_in_documents": True,
        },
    )

    assert result is account
    assert account.bank_name == "Example Bank"
    assert account.show_in_documents is True
    session.flush.assert_awaited_once()
    session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_bank_account_update_audits_before_commit_without_details():
    from services.platform_company_bank_accounts import (
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    account_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=account_id,
        tenant_company_profile_id=profile.id,
        row_version=1,
        label=None,
        account_holder_name="Example Legal Ltd",
        bank_name="Old Bank",
        country_code="PT",
        iban="PT50000201231234567890154",
        bic_swift="CGDIPTPL",
        account_number=None,
        currency="EUR",
        is_primary=False,
        show_in_documents=False,
    )
    calls = []

    async def update_account(*, account, values):
        calls.append(("update", dict(values)))
        for name, value in values.items():
            setattr(account, name, value)
        return account

    async def create_event(**kwargs):
        calls.append(("audit", kwargs))

    async def commit():
        calls.append(("commit",))

    session = SimpleNamespace(
        commit=commit,
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(
                return_value=profile
            ),
        ),
        account_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(
                return_value=account
            ),
            update_account=update_account,
        ),
        event_repository=SimpleNamespace(
            create_event=create_event,
        ),
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    result = await service.update_account(
        actor=actor,
        account_id=account_id,
        expected_row_version=1,
        changes={
            "bank_name": " New Bank ",
            "show_in_documents": True,
        },
    )

    assert result is account
    assert [call[0] for call in calls] == [
        "update", "audit", "commit"
    ]
    assert calls[0][1] == {
        "bank_name": "New Bank",
        "show_in_documents": True,
    }
    event = calls[1][1]
    assert event["tenant_id"] == tenant_id
    assert event["user_id"] == actor.user_id
    assert event["entity_id"] == account_id
    assert "PT50000201231234567890154" not in str(
        event["payload"]
    )
    assert "New Bank" not in str(event["payload"])
    session.rollback.assert_not_awaited()

@pytest.mark.asyncio
async def test_bank_account_update_rolls_back_when_audit_fails():
    from services.platform_company_bank_accounts import (
        CompanyBankAccountOperationError,
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=uuid4(),
        tenant_company_profile_id=profile.id,
        row_version=1,
        label=None,
        account_holder_name="Example Legal Ltd",
        bank_name="Old Bank",
        country_code="PT",
        iban="PT50000201231234567890154",
        bic_swift="CGDIPTPL",
        account_number=None,
        currency="EUR",
        is_primary=False,
        show_in_documents=False,
    )
    account_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=account),
        update_account=AsyncMock(return_value=account),
    )
    event_repository = SimpleNamespace(
        create_event=AsyncMock(
            side_effect=RuntimeError("Private provider details")
        ),
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(
                return_value=profile
            ),
        ),
        account_repository=account_repository,
        event_repository=event_repository,
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    with pytest.raises(CompanyBankAccountOperationError) as error:
        await service.update_account(
            actor=actor,
            account_id=account.id,
            expected_row_version=1,
            changes={"bank_name": "New Bank"},
        )

    account_repository.update_account.assert_awaited_once()
    event_repository.create_event.assert_awaited_once()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    assert "Private provider details" not in str(error.value)

@pytest.mark.asyncio
async def test_bank_account_repository_deactivates_without_delete():
    from database.repositories.company_profiles import (
        TenantCompanyBankAccountRepository,
    )

    account = SimpleNamespace(id=uuid4(), is_active=True)
    session = SimpleNamespace(
        flush=AsyncMock(),
        delete=AsyncMock(),
        commit=AsyncMock(),
    )

    result = await TenantCompanyBankAccountRepository(
        session
    ).deactivate_account(account=account)

    assert result is account
    assert account.is_active is False
    session.flush.assert_awaited_once()
    session.delete.assert_not_awaited()
    session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_bank_account_deactivation_audits_before_commit():
    from services.platform_company_bank_accounts import (
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=uuid4(),
        tenant_company_profile_id=profile.id,
        row_version=3,
        is_active=True,
        iban="PT50000201231234567890154",
    )
    calls = []

    async def deactivate_account(*, account):
        calls.append(("deactivate",))
        account.is_active = False
        return account

    async def create_event(**kwargs):
        calls.append(("audit", kwargs))

    async def commit():
        calls.append(("commit",))

    profile_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(
            return_value=profile
        ),
    )
    account_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(
            return_value=account
        ),
        deactivate_account=deactivate_account,
    )
    session = SimpleNamespace(
        commit=commit,
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=profile_repository,
        account_repository=account_repository,
        event_repository=SimpleNamespace(
            create_event=create_event,
        ),
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    result = await service.deactivate_account(
        actor=actor,
        account_id=account.id,
        expected_row_version=3,
    )

    assert result is account
    assert account.is_active is False
    assert [call[0] for call in calls] == [
        "deactivate", "audit", "commit"
    ]
    profile_repository.get_for_tenant_for_update.assert_awaited_once_with(
        tenant_id=tenant_id,
    )
    account_repository.get_for_tenant_for_update.assert_awaited_once_with(
        tenant_id=tenant_id,
        account_id=account.id,
    )
    event = calls[1][1]
    assert event["tenant_id"] == tenant_id
    assert event["user_id"] == actor.user_id
    assert event["entity_id"] == account.id
    assert "PT50000201231234567890154" not in str(
        event["payload"]
    )
    session.rollback.assert_not_awaited()

@pytest.mark.asyncio
async def test_bank_account_deactivation_rejects_stale_row_version():
    from services.platform_company_bank_accounts import (
        CompanyBankAccountRowVersionConflictError,
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=uuid4(),
        tenant_company_profile_id=profile.id,
        row_version=4,
        is_active=True,
    )
    account_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=account),
        deactivate_account=AsyncMock(),
    )
    event_repository = SimpleNamespace(
        create_event=AsyncMock(),
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(
                return_value=profile
            ),
        ),
        account_repository=account_repository,
        event_repository=event_repository,
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    with pytest.raises(
        CompanyBankAccountRowVersionConflictError
    ) as error:
        await service.deactivate_account(
            actor=actor,
            account_id=account.id,
            expected_row_version=3,
        )

    assert error.value.status_code == 409
    assert error.value.code == "ROW_VERSION_CONFLICT"
    account_repository.deactivate_account.assert_not_awaited()
    event_repository.create_event.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_bank_account_deactivation_rolls_back_on_audit_failure():
    from services.platform_company_bank_accounts import (
        CompanyBankAccountOperationError,
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=uuid4(),
        tenant_company_profile_id=profile.id,
        row_version=1,
        is_active=True,
    )
    calls = []

    async def deactivate_account(*, account):
        calls.append("deactivate")
        account.is_active = False
        return account

    async def fail_audit(**kwargs):
        calls.append("audit")
        raise RuntimeError("Private database details")

    async def rollback():
        calls.append("rollback")

    account_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=account),
        deactivate_account=AsyncMock(
            side_effect=deactivate_account
        ),
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(side_effect=rollback),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(
                return_value=profile
            ),
        ),
        account_repository=account_repository,
        event_repository=SimpleNamespace(
            create_event=fail_audit,
        ),
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    with pytest.raises(CompanyBankAccountOperationError) as error:
        await service.deactivate_account(
            actor=actor,
            account_id=account.id,
            expected_row_version=1,
        )

    assert calls == ["deactivate", "audit", "rollback"]
    account_repository.deactivate_account.assert_awaited_once()
    session.commit.assert_not_awaited()
    assert "Private database details" not in str(error.value)


@pytest.mark.parametrize("operation", ("create", "update"))
@pytest.mark.asyncio
async def test_bank_account_unique_primary_conflict_rolls_back(operation):
    from sqlalchemy.exc import IntegrityError
    from services.platform_company_bank_accounts import (
        CompanyBankAccountConflictError,
        TenantCompanyBankAccountService,
    )

    class UniqueViolation(Exception):
        sqlstate = "23505"
        constraint_name = (
            "uq_tenant_company_bank_accounts_active_primary_currency"
        )

    tenant_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = SimpleNamespace(
        id=uuid4(),
        tenant_company_profile_id=profile.id,
        label="Main",
        account_holder_name="Example Company",
        bank_name=None,
        country_code="PT",
        iban=None,
        bic_swift=None,
        account_number="123456",
        currency="EUR",
        is_primary=False,
        is_active=True,
        show_in_documents=False,
        row_version=1,
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )
    db_error = IntegrityError(
        operation.upper(), {}, UniqueViolation("private DB details")
    )
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    repository = SimpleNamespace(
        create_account=AsyncMock(side_effect=db_error),
        update_account=AsyncMock(side_effect=db_error),
        get_for_tenant_for_update=AsyncMock(return_value=account),
    )
    events = SimpleNamespace(create_event=AsyncMock())
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(return_value=profile)
        ),
        account_repository=repository,
        event_repository=events,
    )

    with pytest.raises(CompanyBankAccountConflictError) as error:
        if operation == "create":
            await service.create_account(
                actor=actor,
                account_holder_name="Example Company",
                account_number="123456",
                currency="EUR",
                is_primary=True,
            )
        else:
            await service.update_account(
                actor=actor,
                account_id=account.id,
                expected_row_version=1,
                changes={"is_primary": True},
            )

    assert error.value.status_code == 409
    assert error.value.code == "company_bank_account_conflict"
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    events.create_event.assert_not_awaited()


@pytest.mark.parametrize("operation", ("update", "deactivate"))
@pytest.mark.parametrize("record_kind", ("missing", "foreign"))
@pytest.mark.asyncio
async def test_bank_account_change_hides_missing_or_foreign_record(
    operation, record_kind,
):
    from services.platform_company_bank_accounts import (
        CompanyBankAccountNotFoundError,
        TenantCompanyBankAccountService,
    )

    tenant_id = uuid4()
    account_id = uuid4()
    profile = SimpleNamespace(id=uuid4(), tenant_id=tenant_id)
    account = (
        None if record_kind == "missing"
        else SimpleNamespace(
            id=account_id,
            tenant_company_profile_id=uuid4(),
        )
    )
    account_repository = SimpleNamespace(
        get_for_tenant_for_update=AsyncMock(return_value=account),
        update_account=AsyncMock(),
        deactivate_account=AsyncMock(),
    )
    event_repository = SimpleNamespace(create_event=AsyncMock())
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(
                return_value=profile
            )
        ),
        account_repository=account_repository,
        event_repository=event_repository,
    )
    actor = SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    with pytest.raises(CompanyBankAccountNotFoundError) as error:
        if operation == "update":
            await service.update_account(
                actor=actor,
                account_id=account_id,
                expected_row_version=1,
                changes={"label": "Updated"},
            )
        else:
            await service.deactivate_account(
                actor=actor,
                account_id=account_id,
                expected_row_version=1,
            )

    assert error.value.code == "company_bank_account_not_found"
    account_repository.update_account.assert_not_awaited()
    account_repository.deactivate_account.assert_not_awaited()
    event_repository.create_event.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize("operation", ("create", "update", "deactivate"))
@pytest.mark.asyncio
async def test_bank_account_operation_requires_existing_company_profile(
    operation,
):
    from services.platform_company_bank_accounts import (
        CompanyBankAccountProfileNotFoundError,
        TenantCompanyBankAccountService,
    )

    session = SimpleNamespace(
        commit=AsyncMock(), rollback=AsyncMock()
    )
    accounts = SimpleNamespace(
        create_account=AsyncMock(),
        get_for_tenant_for_update=AsyncMock(),
        update_account=AsyncMock(),
        deactivate_account=AsyncMock(),
    )
    events = SimpleNamespace(create_event=AsyncMock())
    service = TenantCompanyBankAccountService(
        session=session,
        profile_repository=SimpleNamespace(
            get_for_tenant_for_update=AsyncMock(return_value=None)
        ),
        account_repository=accounts,
        event_repository=events,
    )
    actor = SimpleNamespace(
        tenant_id=uuid4(),
        user_id=uuid4(),
        status="active",
        permissions=("platform.organization.profile.manage",),
    )

    with pytest.raises(CompanyBankAccountProfileNotFoundError) as error:
        if operation == "create":
            await service.create_account(
                actor=actor,
                account_holder_name="Example Company",
                account_number="123456",
                currency="EUR",
            )
        elif operation == "update":
            await service.update_account(
                actor=actor,
                account_id=uuid4(),
                expected_row_version=1,
                changes={"label": "Updated"},
            )
        else:
            await service.deactivate_account(
                actor=actor,
                account_id=uuid4(),
                expected_row_version=1,
            )

    assert error.value.code == "company_profile_not_found"
    accounts.create_account.assert_not_awaited()
    accounts.get_for_tenant_for_update.assert_not_awaited()
    accounts.update_account.assert_not_awaited()
    accounts.deactivate_account.assert_not_awaited()
    events.create_event.assert_not_awaited()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
