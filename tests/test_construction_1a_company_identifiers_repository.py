from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql


@pytest.mark.asyncio
async def test_con_c001_identifier_list_is_scoped_to_tenant():
    from database.repositories.company_profiles import (
        TenantCompanyIdentifierRepository,
    )

    tenant_id = uuid4()

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return FakeResult()

    session = FakeSession()
    result = await TenantCompanyIdentifierRepository(
        session
    ).list_for_tenant(tenant_id=tenant_id)

    assert result == []
    compiled = session.statement.compile(
        dialect=postgresql.dialect()
    )
    sql = str(compiled).lower()
    assert "join tenant_company_profiles" in sql
    assert "tenant_company_profiles.tenant_id" in sql
    assert tenant_id in compiled.params.values()


@pytest.mark.asyncio
async def test_con_c001_identifier_create_does_not_commit():
    from types import SimpleNamespace

    from database.repositories.company_profiles import (
        TenantCompanyIdentifierRepository,
    )

    calls = []

    class FakeSession:
        def add(self, value):
            calls.append(("add", value))

        async def flush(self):
            calls.append(("flush", None))

        async def commit(self):
            pytest.fail("Repository must not commit")

    profile_id = uuid4()
    repository = TenantCompanyIdentifierRepository(FakeSession())
    identifier = await repository.create_identifier(
        profile=SimpleNamespace(id=profile_id),
        country_code="PT",
        identifier_type="vat",
        identifier_value=" PT 123 ",
        identifier_value_normalized="PT123",
        is_primary=True,
    )

    assert identifier.tenant_company_profile_id == profile_id
    assert identifier.identifier_value == " PT 123 "
    assert identifier.identifier_value_normalized == "PT123"
    assert identifier.is_primary is True
    assert calls == [("add", identifier), ("flush", None)]


@pytest.mark.asyncio
async def test_con_c001_identifier_lock_is_tenant_scoped():
    from database.repositories.company_profiles import (
        TenantCompanyIdentifierRepository,
    )

    tenant_id = uuid4()
    identifier_id = uuid4()

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return FakeResult()

    session = FakeSession()
    result = await TenantCompanyIdentifierRepository(
        session
    ).get_for_tenant_for_update(
        tenant_id=tenant_id,
        identifier_id=identifier_id,
    )

    assert result is None
    compiled = session.statement.compile(
        dialect=postgresql.dialect()
    )
    sql = str(compiled).lower()
    assert "join tenant_company_profiles" in sql
    assert "tenant_company_profiles.tenant_id" in sql
    assert "tenant_company_identifiers.id" in sql
    assert "for update" in sql
    assert tenant_id in compiled.params.values()
    assert identifier_id in compiled.params.values()
    assert session.statement.get_execution_options().get(
        "populate_existing"
    ) is True


@pytest.mark.asyncio
async def test_con_c001_identifier_delete_is_physical_without_commit():
    from types import SimpleNamespace

    from database.repositories.company_profiles import (
        TenantCompanyIdentifierRepository,
    )

    calls = []

    class FakeSession:
        async def delete(self, identifier):
            calls.append(("delete", identifier))

        async def flush(self):
            calls.append(("flush", None))

        async def commit(self):
            pytest.fail("Repository must not commit")

    identifier = SimpleNamespace(id=uuid4())
    await TenantCompanyIdentifierRepository(
        FakeSession()
    ).delete_locked_identifier(identifier=identifier)

    assert calls == [
        ("delete", identifier),
        ("flush", None),
    ]
