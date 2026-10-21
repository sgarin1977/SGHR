from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from services.construction_lead_rules import ConstructionLeadInput
from database.repositories.construction_leads import ConstructionLeadRepository
from test_construction_1a_lead_model import TABLE, lead_db
from test_construction_1a_lead_repository import AsyncSessionAdapter


@pytest.mark.parametrize("description", [
    "  Client needs painting and  repair  ",
    "a618b2a0-d43f-4fc9-8820-9b0710c9165c",
    "  Ремонт кухні\nз фарбуванням  ",
])
def test_description_preserves_original_text_without_automatic_classification(description):
    data = ConstructionLeadInput(
        source="partner_portal", name="Client",
        work_type_description=description, phone="123",
    )
    fields = asdict(data)
    assert fields["work_type_description"] == description.strip()
    assert "work_type" not in fields
    assert "master_work_type_id" not in fields and "organization_work_type_id" not in fields
    assert data.status == "new"


@pytest.mark.parametrize("field,value", [
    ("work_type", "Painting"), ("master_work_type_id", uuid4()), ("organization_work_type_id", uuid4()),
])
def test_legacy_field_and_unvalidated_catalogue_references_are_not_creation_inputs(field, value):
    with pytest.raises(TypeError):
        ConstructionLeadInput(
            source="manual", name="Client", work_type_description="Painting",
            email="client@example.com", **{field: value},
        )


def test_storage_uses_description_column_and_nonblank_check():
    assert "work_type_description" in TABLE.c and "work_type" not in TABLE.c
    assert not TABLE.c.work_type_description.nullable
    assert TABLE.c.work_type_description.type.python_type is str
    assert not TABLE.c.work_type_description.foreign_keys
    constraints = {item.name: item for item in TABLE.constraints}
    assert str(constraints["ck_construction_leads_work_type_description"].sqltext) == (
        "length(btrim(work_type_description)) > 0"
    )
    ddl = str(CreateTable(TABLE).compile(dialect=postgresql.dialect()))
    assert "work_type_description TEXT NOT NULL" in ddl
    assert "work_type TEXT NOT NULL" not in ddl


@pytest.mark.asyncio
async def test_repository_round_trip_keeps_description_and_caller_transaction(lead_db):
    engine, ids = lead_db
    adapter = AsyncSessionAdapter(engine)
    try:
        adapter.sync.begin()
        data = ConstructionLeadInput(
            source="new_channel", name="Client", work_type_description=" Painting and  repair ",
            email="client@example.com",
        )
        repo = ConstructionLeadRepository(adapter)
        row = await repo.create_lead(
            tenant_id=ids["a"], data=data, as_of=datetime.now(timezone.utc),
        )
        assert row.work_type_description == "Painting and  repair"
        assert (await repo.get_active_lead(tenant_id=ids["a"], lead_id=row.id)) is row
        assert await repo.get_active_lead(tenant_id=ids["b"], lead_id=row.id) is None
        assert row.row_version == 1 and row.status == "new" and row.source == "new_channel"
        adapter.commit.assert_not_called()
        adapter.rollback.assert_not_called()
        adapter.sync.rollback()
        assert adapter.sync.execute(select(TABLE.c.id)).all() == []
    finally:
        adapter.sync.close()
