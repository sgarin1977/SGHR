from datetime import datetime, timezone
from itertools import product
import re
from uuid import UUID, uuid4

import pytest
from sqlalchemy import BigInteger, ForeignKeyConstraint, UniqueConstraint, insert, select, delete
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateTable, CreateIndex

from database.models import ConstructionActivity, ConstructionLead
from services.construction.construction_activity_rules import CONSTRUCTION_ACTIVITY_TYPES, CONSTRUCTION_ACTIVITY_STATUSES
from test_construction_1a_lead_model import lead_db, values as lead_values

TABLE=ConstructionActivity.__table__


def test_exact_approved_fields_types_nullability_and_no_next_action_column():
    required={"id","tenant_id","type","status","due_at","timezone","created_by_user_id",
        "created_at","updated_at","row_version"}
    optional={"lead_id","client_id","project_id","responsible_user_id","comment","result",
        "completed_at","cancelled_at","deleted_at"}
    assert set(TABLE.c.keys())==required|optional
    assert all(not TABLE.c[name].nullable for name in required)
    assert all(TABLE.c[name].nullable for name in optional)
    for name in ("id","tenant_id","lead_id","client_id","project_id","responsible_user_id","created_by_user_id"):
        assert TABLE.c[name].type.python_type is UUID
    for name in ("type","status","timezone","comment","result"):
        assert TABLE.c[name].type.python_type is str
    for name in ("due_at","completed_at","cancelled_at","created_at","updated_at","deleted_at"):
        assert TABLE.c[name].type.timezone is True
    assert isinstance(TABLE.c.row_version.type,BigInteger)
    assert "next_action" not in ConstructionLead.__table__.c and "next_action" not in TABLE.c


def test_constraints_use_confirmed_types_statuses_and_tenant_composite_links():
    constraints={item.name:item for item in TABLE.constraints}
    for key,expected in [("type",CONSTRUCTION_ACTIVITY_TYPES),("status",CONSTRUCTION_ACTIVITY_STATUSES)]:
        assert set(re.findall("'([^']*)'",str(constraints["ck_construction_activities_"+key].sqltext)))==set(expected)
    for name,parent in [("lead","construction_leads"),("client","construction_clients"),("project","construction_projects")]:
        constraint=constraints["fk_construction_activities_tenant_"+name]
        assert isinstance(constraint,ForeignKeyConstraint)
        assert tuple(constraint.columns.keys())==("tenant_id",name+"_id")
        assert [part.target_fullname for part in constraint.elements]==[parent+".tenant_id",parent+".id"]
        assert constraint.ondelete not in ("CASCADE","SET NULL")
    assert {tuple(item.columns.keys()) for item in TABLE.constraints if isinstance(item,UniqueConstraint)}=={("tenant_id","id")}
    assert next(iter(TABLE.c.created_by_user_id.foreign_keys)).ondelete=="RESTRICT"
    assert next(iter(TABLE.c.responsible_user_id.foreign_keys)).ondelete=="SET NULL"


def test_initial_defaults_versioning_and_utc_server_timestamps():
    assert TABLE.c.status.default.arg=="planned" and str(TABLE.c.status.server_default.arg)=="'planned'"
    assert TABLE.c.row_version.default.arg==1 and str(TABLE.c.row_version.server_default.arg)=="1"
    assert ConstructionActivity.__mapper__.version_id_col is TABLE.c.row_version
    assert ConstructionActivity.__mapper__.version_id_generator(None)==1
    assert ConstructionActivity.__mapper__.version_id_generator(4)==5
    for name in ("created_at","updated_at"):
        assert TABLE.c[name].default.arg(None).tzinfo is timezone.utc
        assert str(TABLE.c[name].server_default.arg)=="now()"


def test_postgresql_ddl_and_next_action_index_compile_with_real_resource_foreign_keys():
    ddl=str(CreateTable(TABLE).compile(dialect=postgresql.dialect()))
    assert "CREATE TABLE construction_activities" in ddl and "row_version BIGINT" in ddl
    assert "due_at TIMESTAMP WITH TIME ZONE NOT NULL" in ddl
    for parent in ("construction_leads","construction_clients","construction_projects"):
        assert "REFERENCES "+parent+" (tenant_id, id)" in ddl
    index=next(item for item in TABLE.indexes if item.name=="ix_construction_activities_next_action_lead")
    index_sql=str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "(tenant_id, lead_id, due_at, id)" in index_sql
    assert "WHERE status = 'planned' AND deleted_at IS NULL" in index_sql


@pytest.fixture
def activity_db(lead_db):
    engine,ids=lead_db
    ids={**ids,"lead_a":uuid4(),"lead_b":uuid4()}
    with engine.begin() as connection:
        for suffix in ("a","b"):
            connection.execute(insert(ConstructionLead.__table__).values(**lead_values(
                ids,id=ids["lead_"+suffix],tenant_id=ids[suffix])))
        TABLE.create(connection)
    return engine,ids


def values(ids,**changes):
    data=dict(id=uuid4(),tenant_id=ids["a"],type="call",status="planned",lead_id=ids["lead_a"],
        due_at=datetime(2026,10,13,12,tzinfo=timezone.utc),timezone="Europe/Lisbon",created_by_user_id=ids["user"])
    data.update(changes)
    return data


@pytest.mark.parametrize("lead,client,project",[combination for combination in product((False,True),repeat=3) if any(combination)])
def test_one_or_multiple_links_are_allowed_without_forcing_conversion_or_assignment(activity_db,lead,client,project):
    engine,ids=activity_db
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids,
            lead_id=ids["lead_a"] if lead else None,client_id=ids["client_a"] if client else None,
            project_id=ids["project_a"] if project else None)))
        stored=connection.execute(select(TABLE)).mappings().one()
        assert stored["responsible_user_id"] is None and stored["result"] is None
        assert stored["row_version"]==1 and stored["status"]=="planned"


@pytest.mark.parametrize("field",["lead_id","client_id","project_id"])
def test_foreign_tenant_links_are_rejected_by_database(activity_db,field):
    engine,ids=activity_db
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(insert(TABLE).values(**values(ids,**{field:ids[field[:-3]+"_b"]})))


@pytest.mark.parametrize("field",["lead_id","client_id","project_id","responsible_user_id","created_by_user_id"])
def test_nonexistent_resource_and_user_references_fail(activity_db,field):
    engine,ids=activity_db
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(insert(TABLE).values(**values(ids,**{field:uuid4()})))


@pytest.mark.parametrize("changes",[
    dict(lead_id=None,client_id=None,project_id=None),dict(type="email"),dict(type=None),dict(status="new"),
    dict(status=None),dict(timezone=""),dict(timezone="   "),dict(timezone=None),dict(due_at=None),
    dict(created_by_user_id=None),dict(row_version=0),dict(row_version=-1),
])
def test_required_data_and_confirmed_codes_are_enforced(activity_db,changes):
    engine,ids=activity_db
    with engine.begin() as connection:
        with pytest.raises(IntegrityError): connection.execute(insert(TABLE).values(**values(ids,**changes)))


@pytest.mark.parametrize("status,completed,cancelled",list(product(("planned","completed","cancelled"),(False,True),(False,True))))
def test_lifecycle_timestamps_match_completed_or_cancelled_outcome(activity_db,status,completed,cancelled):
    engine,ids=activity_db
    instant=datetime(2026,10,13,12,tzinfo=timezone.utc)
    data=values(ids,status=status,completed_at=instant if completed else None,cancelled_at=instant if cancelled else None)
    valid=(status,completed,cancelled) in {("planned",False,False),("completed",True,False),("cancelled",False,True)}
    with engine.begin() as connection:
        if valid: connection.execute(insert(TABLE).values(**data))
        else:
            with pytest.raises(IntegrityError): connection.execute(insert(TABLE).values(**data))


def test_linked_business_record_cannot_be_physically_deleted_out_from_under_history(activity_db):
    engine,ids=activity_db
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids)))
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(delete(ConstructionLead.__table__).where(ConstructionLead.id==ids["lead_a"]))


def test_soft_delete_keeps_row_and_orm_version_rejects_stale_edit(activity_db):
    engine,ids=activity_db
    identifier=uuid4()
    with engine.begin() as connection: connection.execute(insert(TABLE).values(**values(ids,id=identifier)))
    one,two=Session(engine,expire_on_commit=False),Session(engine,expire_on_commit=False)
    try:
        current=one.get(ConstructionActivity,identifier)
        stale=two.get(ConstructionActivity,identifier)
        current.comment="Updated"
        one.commit()
        assert current.row_version==2
        stale.comment="Stale update"
        with pytest.raises(StaleDataError): two.commit()
        two.rollback()
        current.deleted_at=datetime(2026,10,13,12,tzinfo=timezone.utc)
        one.commit()
        assert current.row_version==3
        with engine.connect() as connection:
            stored=connection.execute(select(TABLE).where(TABLE.c.id==identifier)).mappings().one()
            assert stored["deleted_at"] is not None and stored["comment"]=="Updated"
    finally: one.close(); two.close()


def test_result_is_optional_text_and_timezone_is_validated_by_existing_domain_rules(activity_db):
    engine,ids=activity_db
    with engine.begin() as connection:
        connection.execute(insert(TABLE).values(**values(ids,type="task",status="completed",
            completed_at=datetime(2026,10,13,12,tzinfo=timezone.utc),result="Called the client")))
        assert connection.execute(select(TABLE.c.result)).scalar_one()=="Called the client"
    # The DB stores the timezone code; the domain layer validates IANA membership.
    from services.construction.construction_activity_rules import normalize_construction_activity_schedule, ConstructionActivityValidationError
    with pytest.raises(ConstructionActivityValidationError):
        normalize_construction_activity_schedule(due_at=datetime.now(timezone.utc),timezone_code="Europe/Unknown")
