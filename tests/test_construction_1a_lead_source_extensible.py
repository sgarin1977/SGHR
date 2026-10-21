from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import insert, select

import services.construction_lead_rules as rules
from database.repositories.construction_leads import ConstructionLeadRepository
from test_construction_1a_lead_model import TABLE, lead_db, values
from test_construction_1a_lead_repository import AsyncSessionAdapter, repo_case


@pytest.mark.parametrize("code", ["instagram", "partner_portal", "campaign_2026", "external_erp"])
def test_new_source_codes_are_accepted_without_registry_changes(code):
    data = rules.ConstructionLeadInput(
        source=" " + code.upper() + " ", name="Lead",
        work_type_description="Painting", phone="123",
    )
    assert data.source == code and data.status == "new"
    assert not hasattr(rules, "CONSTRUCTION_LEAD_SOURCES")


@pytest.mark.parametrize("code", ["instagram", "partner_portal", "campaign_2026", "external_erp"])
def test_storage_accepts_extended_source_and_preserves_historical_rows(lead_db, code):
    engine, ids = lead_db
    historical, added = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids, id=historical, source="phone")))
        connection.execute(insert(TABLE).values(**values(ids, id=added, source=code)))
        sources = dict(connection.execute(select(TABLE.c.id, TABLE.c.source)).all())
    assert sources == {historical: "phone", added: code}


@pytest.mark.asyncio
async def test_new_source_filter_is_bound_parameter_and_remains_tenant_scoped(repo_case):
    c = repo_case
    await c.repo.list_active_leads(tenant_id=c.tenant_id, source=" PARTNER_PORTAL ")
    statement = c.session.execute.await_args.args[0]
    assert c.tenant_id in statement.compile().params.values()
    assert "partner_portal" in statement.compile().params.values()
    assert "construction_leads.tenant_id =" in str(statement.whereclause)
    assert "construction_leads.deleted_at IS NULL" in str(statement.whereclause)
    c.session.commit.assert_not_called()
    c.session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_real_repository_creates_and_filters_new_code_without_cross_tenant_results(lead_db):
    engine, ids = lead_db
    adapter = AsyncSessionAdapter(engine)
    try:
        adapter.sync.begin()
        repo = ConstructionLeadRepository(adapter)
        data = rules.ConstructionLeadInput(
            source=" Partner_Portal ", name="Lead", work_type_description="Painting", email="lead@example.com",
        )
        created = await repo.create_lead(
            tenant_id=ids["a"], data=data, as_of=datetime.now(timezone.utc),
        )
        assert created.source == "partner_portal"
        assert [row.id for row in await repo.list_active_leads(
            tenant_id=ids["a"], source="partner_portal",
        )] == [created.id]
        assert await repo.list_active_leads(tenant_id=ids["b"], source="partner_portal") == []
        adapter.commit.assert_not_called()
        adapter.rollback.assert_not_called()
        adapter.sync.rollback()
        assert adapter.sync.execute(select(TABLE.c.id)).all() == []
    finally:
        adapter.sync.close()
