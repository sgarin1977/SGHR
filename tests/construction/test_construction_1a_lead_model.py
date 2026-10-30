import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, create_engine, event, insert, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateTable

from database.models import ConstructionLead
from services.construction.construction_lead_rules import CONSTRUCTION_LEAD_STATUSES


TABLE = ConstructionLead.__table__


def test_lead_model_has_exact_approved_fields_types_and_nullability():
    required = {"id", "tenant_id", "source", "name", 'work_type_description', "status", "created_at", "updated_at", "row_version"}
    optional = {"phone", "email", "address_raw", "responsible_user_id", "comment",
                "converted_client_id", "converted_project_id", "deleted_at"}
    assert set(TABLE.columns.keys()) == required | optional
    assert all(not TABLE.c[name].nullable for name in required)
    assert all(TABLE.c[name].nullable for name in optional)
    assert TABLE.c.id.primary_key
    for name in ("id", "tenant_id", "responsible_user_id", "converted_client_id", "converted_project_id"):
        assert TABLE.c[name].type.python_type is UUID
    for name in ("source", "name", "phone", "email", "address_raw", 'work_type_description', "status", "comment"):
        assert TABLE.c[name].type.python_type is str
    assert not TABLE.c.work_type_description.foreign_keys
    assert "next_action" not in TABLE.c and "work_type_id" not in TABLE.c


def test_lead_storage_source_is_extensible_and_statuses_remain_fixed():
    constraints = {constraint.name: constraint for constraint in TABLE.constraints}
    assert str(constraints['ck_construction_leads_source'].sqltext) == 'length(btrim(source)) > 0'
    assert set(re.findall("'([^']*)'", str(constraints['ck_construction_leads_status'].sqltext))) == set(CONSTRUCTION_LEAD_STATUSES)
    ddl = str(CreateTable(TABLE).compile(dialect=postgresql.dialect()))
    assert 'source TEXT NOT NULL' in ddl
    assert 'work_type_description TEXT NOT NULL' in ddl
    assert 'TIMESTAMP WITH TIME ZONE' in ddl
    assert 'qualified' not in ddl and "'proposal'" not in ddl
    assert 'CONSTRAINT ck_construction_leads_contact CHECK' in ddl


def test_lead_conversion_foreign_keys_are_tenant_composites_without_unique_conversion_targets():
    constraints = {constraint.name: constraint for constraint in TABLE.constraints}
    for field, parent, name in (
        ("converted_client_id", "construction_clients", "fk_construction_leads_tenant_converted_client"),
        ("converted_project_id", "construction_projects", "fk_construction_leads_tenant_converted_project"),
    ):
        fk = constraints[name]
        assert isinstance(fk, ForeignKeyConstraint)
        assert tuple(fk.columns.keys()) == ("tenant_id", field)
        assert [item.target_fullname for item in fk.elements] == [parent+".tenant_id", parent+".id"]
        assert fk.ondelete not in ("CASCADE", "SET NULL")
    unique = {
        tuple(item.columns.keys()) for item in TABLE.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert unique == {("tenant_id", "id")}
    assert {"tenants.id"} <= {item.target_fullname for item in TABLE.c.tenant_id.foreign_keys}
    responsible = next(iter(TABLE.c.responsible_user_id.foreign_keys))
    assert responsible.target_fullname == "users.id" and responsible.ondelete == "SET NULL"


def test_lead_defaults_are_new_positive_version_and_utc_instants():
    assert TABLE.c.status.default.arg == "new" and str(TABLE.c.status.server_default.arg) == "'new'"
    assert TABLE.c.row_version.default.arg == 1 and str(TABLE.c.row_version.server_default.arg) == "1"
    assert ConstructionLead.__mapper__.version_id_col is TABLE.c.row_version
    assert ConstructionLead.__mapper__.version_id_generator(None) == 1
    assert ConstructionLead.__mapper__.version_id_generator(4) == 5
    for name in ("created_at", "updated_at", "deleted_at"):
        assert TABLE.c[name].type.timezone is True
    for name in ("created_at", "updated_at"):
        value = TABLE.c[name].default.arg(None)
        assert isinstance(value, datetime) and value.tzinfo is timezone.utc
        assert str(TABLE.c[name].server_default.arg) == "now()"
    assert TABLE.c.updated_at.onupdate.arg(None).tzinfo is timezone.utc


@pytest.fixture
def lead_db():
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def setup(connection, _record):
        connection.create_function("btrim", 1, lambda value: None if value is None else value.strip(" "))
        connection.execute("PRAGMA foreign_keys=ON")
    ids = {
        key: uuid4()
        for key in ("a", "b", "user", "client_a", "client_b", "project_a", "project_b")
    }
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE tenants(id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE users(id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE construction_clients(id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), UNIQUE(tenant_id,id))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE construction_projects(id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), "
            "client_id CHAR(32), UNIQUE(tenant_id,id))"
        )
        for tenant in ("a", "b"):
            connection.exec_driver_sql("INSERT INTO tenants VALUES(?)", (ids[tenant].hex,))
            connection.exec_driver_sql(
                "INSERT INTO construction_clients VALUES(?,?)",
                (ids["client_"+tenant].hex, ids[tenant].hex),
            )
            connection.exec_driver_sql(
                "INSERT INTO construction_projects VALUES(?,?,?)",
                (ids["project_"+tenant].hex, ids[tenant].hex, ids["client_"+tenant].hex),
            )
        connection.exec_driver_sql("INSERT INTO users VALUES(?)", (ids["user"].hex,))
        TABLE.create(connection)
    yield engine, ids
    engine.dispose()


def values(ids, **changes):
    result = dict(
        id=uuid4(), tenant_id=ids["a"], source="phone", name="Example lead",
        work_type_description="Painting", phone="+351 123 456 789", row_version=1,
    )
    result.update(changes)
    return result


@pytest.mark.parametrize('changes', [{'source': ''}, {'source': '   '}, {'source': None}, {'name': ''}, {'name': '   '}, {'work_type_description': ''}, {'work_type_description': '  '}, {'work_type_description': None}, {'phone': None, 'email': None}, {'phone': ' ', 'email': ''}, {'status': 'qualified'}, {'status': 'proposal'}, {'status': 'unknown'}, {'row_version': 0}])
def test_database_rejects_invalid_required_data_and_obsolete_statuses(lead_db, changes):
    engine, ids = lead_db
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(insert(TABLE).values(**values(ids, **changes)))


@pytest.mark.parametrize("field", ["converted_client_id", "converted_project_id"])
def test_database_rejects_conversion_target_from_foreign_tenant(lead_db, field):
    engine, ids = lead_db
    target = ids["client_b" if field == "converted_client_id" else "project_b"]
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(insert(TABLE).values(**values(ids, **{field: target})))


def test_nullable_assignment_and_repeated_existing_conversion_targets_are_supported(lead_db):
    engine, ids = lead_db
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids)))
        for _ in range(2):
            connection.execute(insert(TABLE).values(**values(
                ids, converted_client_id=ids["client_a"], converted_project_id=ids["project_a"],
                responsible_user_id=ids["user"], status="won",
            )))
        rows = connection.execute(select(TABLE)).all()
        assert len(rows) == 3
        assert any(
            row.responsible_user_id is None and row.converted_client_id is None
            and row.converted_project_id is None for row in rows
        )


def test_ordinary_won_status_does_not_force_conversion_references(lead_db):
    engine, ids = lead_db
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids, status="won")))
        row = connection.execute(select(TABLE)).one()
        assert row.status == "won" and row.converted_client_id is None and row.converted_project_id is None


def test_orm_versioning_rejects_stale_update_and_soft_delete_keeps_record(lead_db):
    engine, ids = lead_db
    identifier = uuid4()
    with Session(engine) as initial:
        initial.add(ConstructionLead(**values(ids, id=identifier)))
        initial.commit()
    with Session(engine, expire_on_commit=False) as first, Session(engine, expire_on_commit=False) as stale:
        current = first.get(ConstructionLead, identifier)
        old = stale.get(ConstructionLead, identifier)
        assert current.row_version == old.row_version == 1
        current.comment = "Updated by first writer"
        first.commit()
        assert current.row_version == 2
        old.comment = "Stale write must not win"
        with pytest.raises(StaleDataError):
            stale.commit()
        stale.rollback()
    with Session(engine) as final:
        row = final.get(ConstructionLead, identifier)
        assert row.comment == "Updated by first writer" and row.row_version == 2
        row.deleted_at = datetime.now(timezone.utc)
        final.commit()
        assert final.get(ConstructionLead, identifier).row_version == 3
    with engine.connect() as connection:
        stored = connection.execute(select(TABLE).where(TABLE.c.id == identifier)).one()
        assert stored.deleted_at is not None and stored.comment == "Updated by first writer"
