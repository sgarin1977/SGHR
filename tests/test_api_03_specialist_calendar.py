import pytest

def test_calendar_models_match_database_contract():
    from sqlalchemy import DateTime

    from database.models import (
        ProfessionalCabinetCalendar,
        ProfessionalCabinetCalendarException,
        ProfessionalCabinetWorkInterval,
        ServiceOrder,
    )

    calendar_columns = set(
        ProfessionalCabinetCalendar
        .__table__.columns.keys()
    )
    assert calendar_columns == {
        "id",
        "tenant_id",
        "professional_cabinet_id",
        "timezone",
        "slot_duration_minutes",
        "is_active",
        "created_at",
        "updated_at",
    }

    interval_columns = set(
        ProfessionalCabinetWorkInterval
        .__table__.columns.keys()
    )
    assert interval_columns == {
        "id",
        "tenant_id",
        "calendar_id",
        "weekday",
        "start_time",
        "end_time",
        "is_active",
        "created_at",
        "updated_at",
    }

    exception_columns = set(
        ProfessionalCabinetCalendarException
        .__table__.columns.keys()
    )
    assert exception_columns == {
        "id",
        "tenant_id",
        "calendar_id",
        "exception_date",
        "exception_type",
        "start_time",
        "end_time",
        "reason",
        "created_at",
        "updated_at",
    }

    calendar_fk_targets = {
        fk.target_fullname
        for fk in (
            ProfessionalCabinetCalendar
            .__table__.foreign_keys
        )
    }
    assert {
        "professional_cabinets.tenant_id",
        "professional_cabinets.id",
    } <= calendar_fk_targets

    interval_fk_targets = {
        fk.target_fullname
        for fk in (
            ProfessionalCabinetWorkInterval
            .__table__.foreign_keys
        )
    }
    assert {
        "professional_cabinet_calendars.tenant_id",
        "professional_cabinet_calendars.id",
    } <= interval_fk_targets

    exception_fk_targets = {
        fk.target_fullname
        for fk in (
            ProfessionalCabinetCalendarException
            .__table__.foreign_keys
        )
    }
    assert {
        "professional_cabinet_calendars.tenant_id",
        "professional_cabinet_calendars.id",
    } <= exception_fk_targets

    service_order_columns = (
        ServiceOrder.__table__.columns
    )
    assert "start_at" in service_order_columns
    assert "end_at" in service_order_columns

    assert isinstance(
        service_order_columns["start_at"].type,
        DateTime,
    )
    assert (
        service_order_columns[
            "start_at"
        ].type.timezone
        is True
    )
    assert isinstance(
        service_order_columns["end_at"].type,
        DateTime,
    )
    assert (
        service_order_columns[
            "end_at"
        ].type.timezone
        is True
    )


@pytest.mark.asyncio
async def test_calendar_repository_uses_full_cabinet_owner_scope():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.specialist_calendar import (
        SpecialistCalendarRepository,
    )

    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    expected = SimpleNamespace(
        id=uuid4(),
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return expected

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = SpecialistCalendarRepository(
        session
    )

    result = await repository.get_owned_calendar(
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
    )

    assert result is expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()

    normalized_sql = " ".join(sql.split())

    required_scope = (
        "professional_cabinet_calendars.tenant_id",
        (
            "professional_cabinet_calendars."
            "professional_cabinet_id"
        ),
        "professional_cabinets.tenant_id",
        "professional_cabinets.specialist_id",
        "professional_cabinets.id",
    )

    for value in required_scope:
        assert value in normalized_sql


@pytest.mark.asyncio
async def test_calendar_repository_lists_active_work_intervals_by_tenant():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.specialist_calendar import (
        SpecialistCalendarRepository,
    )

    tenant_id = uuid4()
    calendar_id = uuid4()
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = SpecialistCalendarRepository(
        session
    )

    result = await (
        repository.list_active_work_intervals(
            tenant_id=tenant_id,
            calendar_id=calendar_id,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()

    normalized_sql = " ".join(sql.split())

    required_scope = (
        (
            "professional_cabinet_work_"
            "intervals.tenant_id"
        ),
        (
            "professional_cabinet_work_"
            "intervals.calendar_id"
        ),
        (
            "professional_cabinet_work_"
            "intervals.is_active is true"
        ),
        (
            "professional_cabinet_work_"
            "intervals.weekday"
        ),
        (
            "professional_cabinet_work_"
            "intervals.start_time"
        ),
    )

    for value in required_scope:
        assert value in normalized_sql


@pytest.mark.asyncio
async def test_neutral_calendar_uses_owned_cabinet_scope():
    from datetime import time
    from types import SimpleNamespace
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarService,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsAction,
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calendar_id = uuid4()
    interval_id = uuid4()
    calls = []

    actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(
                ("require_cabinet", kwargs)
            )
            return SpecialistCabinetsAction(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeRepository:
        async def get_owned_calendar(
            self,
            **kwargs,
        ):
            calls.append(
                ("get_calendar", kwargs)
            )
            return SimpleNamespace(
                id=calendar_id,
                tenant_id=tenant_id,
                professional_cabinet_id=(
                    cabinet_id
                ),
                timezone="Europe/Kyiv",
                slot_duration_minutes=30,
                is_active=True,
            )

        async def list_active_work_intervals(
            self,
            **kwargs,
        ):
            calls.append(
                ("list_intervals", kwargs)
            )
            return [
                SimpleNamespace(
                    id=interval_id,
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                )
            ]

    service = SpecialistCalendarService(
        session=None,
        cabinets=FakeCabinets(),
        repository=FakeRepository(),
    )

    result = await service.get_calendar_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
    )

    assert calls == [
        (
            "require_cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "get_calendar",
            {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "list_intervals",
            {
                "tenant_id": tenant_id,
                "calendar_id": calendar_id,
            },
        ),
    ]

    assert (
        result.result.professional_cabinet_id
        == cabinet_id
    )
    assert result.result.timezone == (
        "Europe/Kyiv"
    )
    assert (
        result.result.slot_duration_minutes
        == 30
    )
    assert result.result.is_active is True
    assert len(
        result.result.work_intervals
    ) == 1

    interval = (
        result.result.work_intervals[0]
    )
    assert interval.id == interval_id
    assert interval.weekday == 1
    assert interval.start_time == time(9, 0)
    assert interval.end_time == time(17, 0)

    assert not hasattr(
        result.result,
        "tenant_id",
    )
    assert not hasattr(
        result.result,
        "specialist_id",
    )
    assert not hasattr(
        result.result,
        "calendar_id",
    )


@pytest.mark.asyncio
async def test_specialist_calendar_endpoint_uses_current_actor():
    from datetime import time
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_calendar import (
        SpecialistCalendarAction,
        SpecialistCalendarView,
        SpecialistCalendarWorkIntervalView,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    interval_id = uuid4()
    request_id = "specialist-calendar-request"
    calls = []

    api_actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.read",
        ),
    )

    service_actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    result = SpecialistCalendarAction(
        actor=service_actor,
        result=SpecialistCalendarView(
            professional_cabinet_id=(
                cabinet_id
            ),
            timezone="Europe/Kyiv",
            slot_duration_minutes=30,
            is_active=True,
            work_intervals=(
                SpecialistCalendarWorkIntervalView(
                    id=interval_id,
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                ),
            ),
        ),
    )

    class FakeCalendarService:
        async def get_calendar_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return result

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: api_actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: FakeCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
        }
    ]

    assert response.json() == {
        "data": {
            "professional_cabinet_id": (
                str(cabinet_id)
            ),
            "timezone": "Europe/Kyiv",
            "slot_duration_minutes": 30,
            "is_active": True,
            "work_intervals": [
                {
                    "id": str(interval_id),
                    "weekday": 1,
                    "start_time": "09:00:00",
                    "end_time": "17:00:00",
                }
            ],
        },
        "meta": {},
        "request_id": request_id,
    }




@pytest.mark.asyncio
async def test_specialist_calendar_requires_read_permission():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    cabinet_id = uuid4()
    request_id = (
        "specialist-calendar-permission"
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(),
    )

    class ForbiddenCalendarService:
        async def get_calendar_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Calendar service must not be "
                "called without permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: ForbiddenCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "permission_required",
            "message": (
                "Required permission "
                "is not available."
            ),
            "request_id": request_id,
        },
    }



@pytest.mark.asyncio
async def test_specialist_calendar_requires_authentication():
    from uuid import uuid4

    import httpx

    from api.app import create_app

    cabinet_id = uuid4()
    request_id = (
        "specialist-calendar-authentication"
    )

    application = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": (
                "Authentication required."
            ),
            "request_id": request_id,
        },
    }



@pytest.mark.asyncio
async def test_specialist_calendar_hides_missing_or_foreign_cabinet():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_calendar import (
        SpecialistCalendarNotFoundError,
    )

    cabinet_id = uuid4()
    request_id = (
        "specialist-calendar-not-found"
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.read",
        ),
    )

    class MissingCalendarService:
        async def get_calendar_for_user(
            self,
            **kwargs,
        ):
            raise (
                SpecialistCalendarNotFoundError(
                    "Private database details."
                )
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: MissingCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "calendar_not_found",
            "message": (
                "Professional cabinet "
                "calendar was not found."
            ),
            "request_id": request_id,
        },
    }

    assert (
        "Private database details."
        not in response.text
    )



def test_specialist_calendar_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/calendar"
        )
    ]["get"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCalendarResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistCalendarResponse"
        ]["properties"]
    ) == {
        "data",
        "meta",
        "request_id",
    }

    assert set(
        components[
            "SpecialistCalendarData"
        ]["properties"]
    ) == {
        "professional_cabinet_id",
        "timezone",
        "slot_duration_minutes",
        "is_active",
        "work_intervals",
    }

    assert set(
        components[
            "SpecialistCalendarWorkIntervalItem"
        ]["properties"]
    ) == {
        "id",
        "weekday",
        "start_time",
        "end_time",
    }



@pytest.mark.asyncio
async def test_neutral_calendar_schedule_update_uses_owned_cabinet_scope():
    from datetime import time
    from types import SimpleNamespace
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarScheduleInterval,
        SpecialistCalendarService,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsAction,
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calendar_id = uuid4()
    interval_id = uuid4()
    calls = []

    actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(
                ("require_cabinet", kwargs)
            )
            return SpecialistCabinetsAction(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeRepository:
        async def get_owned_calendar(
            self,
            **kwargs,
        ):
            calls.append(
                ("get_calendar", kwargs)
            )
            return SimpleNamespace(
                id=calendar_id,
                timezone="UTC",
                slot_duration_minutes=60,
                is_active=True,
            )

        async def replace_schedule(
            self,
            **kwargs,
        ):
            calls.append(
                ("replace_schedule", kwargs)
            )
            return [
                SimpleNamespace(
                    id=interval_id,
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                )
            ]

    class FakeCalendarEvents:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(
                ("create_event", kwargs)
            )

    session = FakeSession()
    repository = FakeRepository()

    service = SpecialistCalendarService(
        session=session,
        cabinets=FakeCabinets(),
        repository=repository,
        event_repository=(
            FakeCalendarEvents()
        ),
    )

    intervals = (
        SpecialistCalendarScheduleInterval(
            weekday=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
        ),
    )

    action = await (
        service.update_schedule_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=(
                cabinet_id
            ),
            timezone="Europe/Kyiv",
            slot_duration_minutes=30,
            work_intervals=intervals,
            platform="api",
        )
    )

    assert calls == [
        (
            "require_cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "get_calendar",
            {
                "tenant_id": tenant_id,
                "specialist_id": (
                    specialist_id
                ),
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "replace_schedule",
            {
                "tenant_id": tenant_id,
                "calendar_id": calendar_id,
                "timezone": "Europe/Kyiv",
                "slot_duration_minutes": 30,
                "work_intervals": intervals,
            },
        ),
        (
            "create_event",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "event_type": (
                    "change_submitted"
                ),
                "entity_type": (
                    "professional_cabinet_calendar"
                ),
                "entity_id": calendar_id,
                "payload": {
                    "field": "weekly_schedule",
                    "timezone": (
                        "Europe/Kyiv"
                    ),
                    "slot_duration_minutes": 30,
                    "work_intervals_count": 1,
                },
                "platform": "api",
            },
        ),
    ]

    assert session.commits == 1
    assert session.rollbacks == 0

    assert (
        action.result.professional_cabinet_id
        == cabinet_id
    )
    assert (
        action.result.timezone
        == "Europe/Kyiv"
    )
    assert (
        action.result.slot_duration_minutes
        == 30
    )
    assert len(
        action.result.work_intervals
    ) == 1



@pytest.mark.asyncio
async def test_calendar_repository_replaces_tenant_scoped_schedule():
    from datetime import time
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.specialist_calendar import (
        SpecialistCalendarRepository,
    )
    from services.specialist_calendar import (
        SpecialistCalendarScheduleInterval,
    )

    tenant_id = uuid4()
    calendar_id = uuid4()

    intervals = (
        SpecialistCalendarScheduleInterval(
            weekday=1,
            start_time=time(9, 0),
            end_time=time(13, 0),
        ),
        SpecialistCalendarScheduleInterval(
            weekday=1,
            start_time=time(14, 0),
            end_time=time(18, 0),
        ),
    )

    class FakeResult:
        rowcount = 1

    class FakeSession:
        def __init__(self):
            self.statements = []
            self.added = []
            self.flushes = 0

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

        def add_all(self, rows):
            self.added.extend(rows)

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = SpecialistCalendarRepository(
        session
    )

    result = await repository.replace_schedule(
        tenant_id=tenant_id,
        calendar_id=calendar_id,
        timezone="Europe/Kyiv",
        slot_duration_minutes=30,
        work_intervals=intervals,
    )

    assert result == session.added
    assert len(session.statements) == 2
    assert len(session.added) == 2
    assert session.flushes == 1

    statements = [
        " ".join(
            str(
                statement.compile(
                    dialect=(
                        postgresql.dialect()
                    ),
                    compile_kwargs={
                        "literal_binds": True,
                    },
                )
            ).lower().split()
        )
        for statement
        in session.statements
    ]

    update_sql, delete_sql = statements

    assert update_sql.startswith(
        "update professional_cabinet_calendars"
    )
    assert (
        "professional_cabinet_calendars."
        "tenant_id"
        in update_sql
    )
    assert (
        "professional_cabinet_calendars.id"
        in update_sql
    )
    assert "timezone=" in update_sql
    assert "slot_duration_minutes=" in (
        update_sql
    )

    assert delete_sql.startswith(
        "delete from "
        "professional_cabinet_work_intervals"
    )
    assert (
        "professional_cabinet_work_intervals."
        "tenant_id"
        in delete_sql
    )
    assert (
        "professional_cabinet_work_intervals."
        "calendar_id"
        in delete_sql
    )

    first, second = session.added

    assert first.tenant_id == tenant_id
    assert first.calendar_id == calendar_id
    assert first.weekday == 1
    assert first.start_time == time(9, 0)
    assert first.end_time == time(13, 0)
    assert first.is_active is True

    assert second.tenant_id == tenant_id
    assert second.calendar_id == calendar_id
    assert second.weekday == 1
    assert second.start_time == time(14, 0)
    assert second.end_time == time(18, 0)
    assert second.is_active is True



@pytest.mark.asyncio
async def test_specialist_calendar_schedule_update_uses_current_actor():
    from datetime import time
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_calendar import (
        SpecialistCalendarAction,
        SpecialistCalendarScheduleInterval,
        SpecialistCalendarView,
        SpecialistCalendarWorkIntervalView,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    interval_id = uuid4()
    request_id = (
        "specialist-calendar-schedule"
    )
    calls = []

    api_actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.write",
        ),
    )

    service_actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    action = SpecialistCalendarAction(
        actor=service_actor,
        result=SpecialistCalendarView(
            professional_cabinet_id=(
                cabinet_id
            ),
            timezone="Europe/Kyiv",
            slot_duration_minutes=30,
            is_active=True,
            work_intervals=(
                SpecialistCalendarWorkIntervalView(
                    id=interval_id,
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                ),
            ),
        ),
    )

    class FakeCalendarService:
        async def update_schedule_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return action

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: api_actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: FakeCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar/"
                "schedule"
            ),
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "timezone": "Europe/Kyiv",
                "slot_duration_minutes": 30,
                "work_intervals": [
                    {
                        "weekday": 1,
                        "start_time": (
                            "09:00:00"
                        ),
                        "end_time": (
                            "17:00:00"
                        ),
                    }
                ],
            },
        )

    assert response.status_code == 200

    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "timezone": "Europe/Kyiv",
            "slot_duration_minutes": 30,
            "work_intervals": (
                SpecialistCalendarScheduleInterval(
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                ),
            ),
            "platform": "api",
        }
    ]

    assert response.json() == {
        "data": {
            "professional_cabinet_id": (
                str(cabinet_id)
            ),
            "timezone": "Europe/Kyiv",
            "slot_duration_minutes": 30,
            "is_active": True,
            "work_intervals": [
                {
                    "id": str(interval_id),
                    "weekday": 1,
                    "start_time": "09:00:00",
                    "end_time": "17:00:00",
                }
            ],
        },
        "meta": {},
        "request_id": request_id,
    }



@pytest.mark.asyncio
async def test_specialist_calendar_schedule_requires_write_permission():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    cabinet_id = uuid4()
    request_id = (
        "calendar-schedule-write-permission"
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.read",
        ),
    )

    class ForbiddenCalendarService:
        async def update_schedule_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Calendar service must not be "
                "called without write permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: ForbiddenCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar/"
                "schedule"
            ),
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "timezone": "Europe/Kyiv",
                "slot_duration_minutes": 30,
                "work_intervals": [],
            },
        )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "permission_required",
            "message": (
                "Required permission "
                "is not available."
            ),
            "request_id": request_id,
        },
    }



@pytest.mark.asyncio
async def test_calendar_schedule_rejects_invalid_domain_values():
    from datetime import time
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarScheduleInterval,
        SpecialistCalendarService,
        SpecialistCalendarValidationError,
    )

    class ForbiddenCabinets:
        def __init__(self):
            self.calls = 0

        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            self.calls += 1
            raise AssertionError(
                "Owner lookup must not run for "
                "invalid schedule data."
            )

    valid_interval = (
        SpecialistCalendarScheduleInterval(
            weekday=1,
            start_time=time(9, 0),
            end_time=time(17, 0),
        ),
    )

    cases = (
        {
            "timezone": (
                "Not/A_Real_Timezone"
            ),
            "slot_duration_minutes": 30,
            "work_intervals": (
                valid_interval
            ),
        },
        {
            "timezone": "Europe/Kyiv",
            "slot_duration_minutes": 0,
            "work_intervals": (
                valid_interval
            ),
        },
        {
            "timezone": "Europe/Kyiv",
            "slot_duration_minutes": 30,
            "work_intervals": (
                SpecialistCalendarScheduleInterval(
                    weekday=0,
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                ),
            ),
        },
        {
            "timezone": "Europe/Kyiv",
            "slot_duration_minutes": 30,
            "work_intervals": (
                SpecialistCalendarScheduleInterval(
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(9, 0),
                ),
            ),
        },
        {
            "timezone": "Europe/Kyiv",
            "slot_duration_minutes": 30,
            "work_intervals": (
                SpecialistCalendarScheduleInterval(
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(13, 0),
                ),
                SpecialistCalendarScheduleInterval(
                    weekday=1,
                    start_time=time(12, 0),
                    end_time=time(17, 0),
                ),
            ),
        },
    )

    for case in cases:
        cabinets = ForbiddenCabinets()

        service = SpecialistCalendarService(
            session=object(),
            cabinets=cabinets,
            repository=object(),
            event_repository=object(),
        )

        with pytest.raises(
            SpecialistCalendarValidationError
        ):
            await (
                service.update_schedule_for_user(
                    user_id=uuid4(),
                    tenant_id=uuid4(),
                    language="uk",
                    professional_cabinet_id=(
                        uuid4()
                    ),
                    timezone=case[
                        "timezone"
                    ],
                    slot_duration_minutes=case[
                        "slot_duration_minutes"
                    ],
                    work_intervals=case[
                        "work_intervals"
                    ],
                    platform="api",
                )
            )

        assert cabinets.calls == 0



@pytest.mark.asyncio
async def test_calendar_schedule_hides_validation_details():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_calendar import (
        SpecialistCalendarValidationError,
    )

    cabinet_id = uuid4()
    request_id = (
        "calendar-schedule-validation"
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.write",
        ),
    )

    class InvalidCalendarService:
        async def update_schedule_for_user(
            self,
            **kwargs,
        ):
            raise (
                SpecialistCalendarValidationError(
                    "Private database details."
                )
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: InvalidCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar/"
                "schedule"
            ),
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "timezone": "Europe/Kyiv",
                "slot_duration_minutes": 30,
                "work_intervals": [],
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": (
                "calendar_validation_error"
            ),
            "message": (
                "Calendar schedule "
                "is not valid."
            ),
            "request_id": request_id,
        },
    }
    assert (
        "Private database details."
        not in response.text
    )



@pytest.mark.asyncio
async def test_calendar_schedule_rejects_actor_fields():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    request_id = (
        "calendar-schedule-actor-fields"
    )

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.write",
        ),
    )

    class ForbiddenCalendarService:
        async def update_schedule_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Invalid request body must not "
                "reach Calendar service."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: ForbiddenCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar/"
                "schedule"
            ),
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "timezone": "Europe/Kyiv",
                "slot_duration_minutes": 30,
                "work_intervals": [],
                "user_id": str(uuid4()),
                "tenant_id": str(uuid4()),
                "professional_cabinet_id": (
                    str(uuid4())
                ),
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": (
                "Request validation failed."
            ),
            "request_id": request_id,
        },
    }



def test_calendar_schedule_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/calendar/schedule"
        )
    ]["put"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    request_schema = (
        operation["requestBody"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert request_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCalendarScheduleUpdateRequest"
        )
    }

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCalendarResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistCalendarScheduleUpdateRequest"
        ]["properties"]
    ) == {
        "timezone",
        "slot_duration_minutes",
        "work_intervals",
    }

    assert set(
        components[
            "SpecialistCalendarScheduleIntervalRequest"
        ]["properties"]
    ) == {
        "weekday",
        "start_time",
        "end_time",
    }



@pytest.mark.asyncio
async def test_calendar_repository_lists_exceptions_by_tenant():
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.specialist_calendar import (
        SpecialistCalendarRepository,
    )

    tenant_id = uuid4()
    calendar_id = uuid4()
    expected = [
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4()),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = SpecialistCalendarRepository(
        session
    )

    result = await repository.list_exceptions(
        tenant_id=tenant_id,
        calendar_id=calendar_id,
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()
    normalized_sql = " ".join(sql.split())

    required = (
        (
            "professional_cabinet_calendar_"
            "exceptions.tenant_id"
        ),
        (
            "professional_cabinet_calendar_"
            "exceptions.calendar_id"
        ),
        (
            "professional_cabinet_calendar_"
            "exceptions.exception_date"
        ),
        (
            "professional_cabinet_calendar_"
            "exceptions.start_time"
        ),
    )

    for value in required:
        assert value in normalized_sql

    assert "order by" in normalized_sql



@pytest.mark.asyncio
async def test_neutral_calendar_exception_list_uses_owned_cabinet_scope():
    from datetime import date, time
    from types import SimpleNamespace
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarExceptionListAction,
        SpecialistCalendarService,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsAction,
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calendar_id = uuid4()
    exception_id = uuid4()
    calls = []

    actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(
                ("require_cabinet", kwargs)
            )
            return SpecialistCabinetsAction(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeRepository:
        async def get_owned_calendar(
            self,
            **kwargs,
        ):
            calls.append(
                ("get_calendar", kwargs)
            )
            return SimpleNamespace(
                id=calendar_id,
            )

        async def list_exceptions(
            self,
            **kwargs,
        ):
            calls.append(
                ("list_exceptions", kwargs)
            )
            return [
                SimpleNamespace(
                    id=exception_id,
                    tenant_id=tenant_id,
                    calendar_id=calendar_id,
                    exception_date=date(
                        2026,
                        9,
                        1,
                    ),
                    exception_type=(
                        "unavailable"
                    ),
                    start_time=time(12, 0),
                    end_time=time(14, 0),
                    reason="Private appointment",
                )
            ]

    service = SpecialistCalendarService(
        session=None,
        cabinets=FakeCabinets(),
        repository=FakeRepository(),
        event_repository=object(),
    )

    action = await (
        service.list_exceptions_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=(
                cabinet_id
            ),
        )
    )

    assert isinstance(
        action,
        SpecialistCalendarExceptionListAction,
    )

    assert calls == [
        (
            "require_cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "get_calendar",
            {
                "tenant_id": tenant_id,
                "specialist_id": (
                    specialist_id
                ),
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "list_exceptions",
            {
                "tenant_id": tenant_id,
                "calendar_id": calendar_id,
            },
        ),
    ]

    assert len(action.result) == 1
    item = action.result[0]

    assert item.id == exception_id
    assert item.exception_date == date(
        2026,
        9,
        1,
    )
    assert item.exception_type == (
        "unavailable"
    )
    assert item.start_time == time(12, 0)
    assert item.end_time == time(14, 0)
    assert item.reason == (
        "Private appointment"
    )

    assert not hasattr(item, "tenant_id")
    assert not hasattr(item, "calendar_id")



@pytest.mark.asyncio
async def test_specialist_calendar_exceptions_use_current_actor():
    from datetime import date, time
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_calendar import (
        SpecialistCalendarExceptionListAction,
        SpecialistCalendarExceptionView,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    exception_id = uuid4()
    request_id = (
        "specialist-calendar-exceptions"
    )
    calls = []

    api_actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.read",
        ),
    )

    service_actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    action = (
        SpecialistCalendarExceptionListAction(
            actor=service_actor,
            result=(
                SpecialistCalendarExceptionView(
                    id=exception_id,
                    exception_date=date(
                        2026,
                        9,
                        1,
                    ),
                    exception_type=(
                        "unavailable"
                    ),
                    start_time=time(12, 0),
                    end_time=time(14, 0),
                    reason=(
                        "Private appointment"
                    ),
                ),
            ),
        )
    )

    class FakeCalendarService:
        async def list_exceptions_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return action

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: api_actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: FakeCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar/"
                "exceptions"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
        }
    ]

    assert response.json() == {
        "data": [
            {
                "id": str(exception_id),
                "exception_date": "2026-09-01",
                "exception_type": (
                    "unavailable"
                ),
                "start_time": "12:00:00",
                "end_time": "14:00:00",
                "reason": (
                    "Private appointment"
                ),
            }
        ],
        "meta": {},
        "request_id": request_id,
    }



@pytest.mark.asyncio
async def test_calendar_repository_creates_tenant_scoped_exception():
    from datetime import date, time
    from uuid import uuid4

    from database.repositories.specialist_calendar import (
        SpecialistCalendarRepository,
    )

    tenant_id = uuid4()
    calendar_id = uuid4()

    class FakeSession:
        def __init__(self):
            self.added = []
            self.flushes = 0

        def add(self, row):
            self.added.append(row)

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = SpecialistCalendarRepository(
        session
    )

    result = await repository.create_exception(
        tenant_id=tenant_id,
        calendar_id=calendar_id,
        exception_date=date(2026, 9, 1),
        exception_type="unavailable",
        start_time=time(12, 0),
        end_time=time(14, 0),
        reason="Private appointment",
    )

    assert session.added == [result]
    assert session.flushes == 1

    assert result.tenant_id == tenant_id
    assert result.calendar_id == calendar_id
    assert result.exception_date == date(
        2026,
        9,
        1,
    )
    assert result.exception_type == (
        "unavailable"
    )
    assert result.start_time == time(12, 0)
    assert result.end_time == time(14, 0)
    assert result.reason == (
        "Private appointment"
    )



@pytest.mark.asyncio
async def test_neutral_calendar_exception_create_is_scoped_and_audited():
    from datetime import date, time
    from types import SimpleNamespace
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarExceptionAction,
        SpecialistCalendarService,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsAction,
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calendar_id = uuid4()
    exception_id = uuid4()
    exception_date = date(2026, 9, 1)
    start_time = time(12, 0)
    end_time = time(14, 0)
    calls = []

    actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(
                ("require_cabinet", kwargs)
            )
            return SpecialistCabinetsAction(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeRepository:
        async def get_owned_calendar(
            self,
            **kwargs,
        ):
            calls.append(
                ("get_calendar", kwargs)
            )
            return SimpleNamespace(
                id=calendar_id,
            )

        async def create_exception(
            self,
            **kwargs,
        ):
            calls.append(
                ("create_exception", kwargs)
            )
            return SimpleNamespace(
                id=exception_id,
                exception_date=(
                    exception_date
                ),
                exception_type=(
                    "unavailable"
                ),
                start_time=start_time,
                end_time=end_time,
                reason="Private appointment",
            )

    class FakeEvents:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(
                ("create_event", kwargs)
            )

    session = FakeSession()
    service = SpecialistCalendarService(
        session=session,
        cabinets=FakeCabinets(),
        repository=FakeRepository(),
        event_repository=FakeEvents(),
    )

    action = await (
        service.create_exception_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=(
                cabinet_id
            ),
            exception_date=exception_date,
            exception_type="unavailable",
            start_time=start_time,
            end_time=end_time,
            reason=" Private appointment ",
            platform="api",
        )
    )

    assert isinstance(
        action,
        SpecialistCalendarExceptionAction,
    )

    assert calls == [
        (
            "require_cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "get_calendar",
            {
                "tenant_id": tenant_id,
                "specialist_id": (
                    specialist_id
                ),
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "create_exception",
            {
                "tenant_id": tenant_id,
                "calendar_id": calendar_id,
                "exception_date": (
                    exception_date
                ),
                "exception_type": (
                    "unavailable"
                ),
                "start_time": start_time,
                "end_time": end_time,
                "reason": (
                    "Private appointment"
                ),
            },
        ),
        (
            "create_event",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "event_type": (
                    "change_submitted"
                ),
                "entity_type": (
                    "professional_cabinet_"
                    "calendar_exception"
                ),
                "entity_id": exception_id,
                "payload": {
                    "professional_cabinet_id": (
                        str(cabinet_id)
                    ),
                    "exception_date": (
                        "2026-09-01"
                    ),
                    "exception_type": (
                        "unavailable"
                    ),
                    "has_time_interval": True,
                },
                "platform": "api",
            },
        ),
    ]

    assert session.commits == 1
    assert session.rollbacks == 0

    assert action.result.id == exception_id
    assert (
        action.result.exception_date
        == exception_date
    )
    assert (
        action.result.exception_type
        == "unavailable"
    )
    assert action.result.start_time == (
        start_time
    )
    assert action.result.end_time == end_time
    assert action.result.reason == (
        "Private appointment"
    )



@pytest.mark.asyncio
async def test_calendar_exception_rejects_invalid_domain_values():
    from datetime import date, time
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarService,
        SpecialistCalendarValidationError,
    )

    class ForbiddenCabinets:
        def __init__(self):
            self.calls = 0

        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            self.calls += 1
            raise AssertionError(
                "Owner lookup must not run for "
                "invalid exception data."
            )

    cases = (
        {
            "exception_type": "blocked",
            "start_time": None,
            "end_time": None,
        },
        {
            "exception_type": "unavailable",
            "start_time": time(12, 0),
            "end_time": None,
        },
        {
            "exception_type": "available",
            "start_time": None,
            "end_time": time(14, 0),
        },
        {
            "exception_type": "unavailable",
            "start_time": time(14, 0),
            "end_time": time(12, 0),
        },
        {
            "exception_type": "available",
            "start_time": time(12, 0),
            "end_time": time(12, 0),
        },
    )

    for case in cases:
        cabinets = ForbiddenCabinets()

        service = SpecialistCalendarService(
            session=object(),
            cabinets=cabinets,
            repository=object(),
            event_repository=object(),
        )

        with pytest.raises(
            SpecialistCalendarValidationError
        ):
            await (
                service.create_exception_for_user(
                    user_id=uuid4(),
                    tenant_id=uuid4(),
                    language="uk",
                    professional_cabinet_id=(
                        uuid4()
                    ),
                    exception_date=date(
                        2026,
                        9,
                        1,
                    ),
                    exception_type=case[
                        "exception_type"
                    ],
                    start_time=case[
                        "start_time"
                    ],
                    end_time=case[
                        "end_time"
                    ],
                    reason=None,
                    platform="api",
                )
            )

        assert cabinets.calls == 0



@pytest.mark.asyncio
async def test_specialist_calendar_exception_create_uses_current_actor():
    from datetime import date, time
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_calendar import (
        SpecialistCalendarExceptionAction,
        SpecialistCalendarExceptionView,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    exception_id = uuid4()
    request_id = (
        "calendar-exception-create"
    )
    calls = []

    api_actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.write",
        ),
    )

    service_actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    action = SpecialistCalendarExceptionAction(
        actor=service_actor,
        result=(
            SpecialistCalendarExceptionView(
                id=exception_id,
                exception_date=date(
                    2026,
                    9,
                    1,
                ),
                exception_type=(
                    "unavailable"
                ),
                start_time=time(12, 0),
                end_time=time(14, 0),
                reason=(
                    "Private appointment"
                ),
            )
        ),
    )

    class FakeCalendarService:
        async def create_exception_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return action

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: api_actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: FakeCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar/"
                "exceptions"
            ),
            headers={
                "X-Request-ID": request_id,
            },
            json={
                "exception_date": (
                    "2026-09-01"
                ),
                "exception_type": (
                    "unavailable"
                ),
                "start_time": "12:00:00",
                "end_time": "14:00:00",
                "reason": (
                    "Private appointment"
                ),
            },
        )

    assert response.status_code == 201

    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "exception_date": date(
                2026,
                9,
                1,
            ),
            "exception_type": (
                "unavailable"
            ),
            "start_time": time(12, 0),
            "end_time": time(14, 0),
            "reason": (
                "Private appointment"
            ),
            "platform": "api",
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(exception_id),
            "exception_date": "2026-09-01",
            "exception_type": (
                "unavailable"
            ),
            "start_time": "12:00:00",
            "end_time": "14:00:00",
            "reason": (
                "Private appointment"
            ),
        },
        "meta": {},
        "request_id": request_id,
    }



@pytest.mark.asyncio
async def test_calendar_repository_deletes_fully_scoped_exception():
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.specialist_calendar import (
        SpecialistCalendarRepository,
    )

    tenant_id = uuid4()
    calendar_id = uuid4()
    exception_id = uuid4()

    class FakeResult:
        def scalar_one_or_none(self):
            return exception_id

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = SpecialistCalendarRepository(
        session
    )

    result = await repository.delete_exception(
        tenant_id=tenant_id,
        calendar_id=calendar_id,
        exception_id=exception_id,
    )

    assert result is True
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()
    normalized_sql = " ".join(sql.split())

    assert normalized_sql.startswith(
        "delete from "
        "professional_cabinet_calendar_exceptions"
    )

    required_scope = (
        (
            "professional_cabinet_calendar_"
            "exceptions.tenant_id"
        ),
        (
            "professional_cabinet_calendar_"
            "exceptions.calendar_id"
        ),
        (
            "professional_cabinet_calendar_"
            "exceptions.id"
        ),
        "returning",
    )

    for value in required_scope:
        assert value in normalized_sql



@pytest.mark.asyncio
async def test_neutral_calendar_exception_delete_is_scoped_and_audited():
    from types import SimpleNamespace
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarExceptionDeleteAction,
        SpecialistCalendarService,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsAction,
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calendar_id = uuid4()
    exception_id = uuid4()
    calls = []

    actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(
                ("require_cabinet", kwargs)
            )
            return SpecialistCabinetsAction(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeRepository:
        async def get_owned_calendar(
            self,
            **kwargs,
        ):
            calls.append(
                ("get_calendar", kwargs)
            )
            return SimpleNamespace(
                id=calendar_id,
            )

        async def delete_exception(
            self,
            **kwargs,
        ):
            calls.append(
                ("delete_exception", kwargs)
            )
            return True

    class FakeEvents:
        async def create_event(
            self,
            **kwargs,
        ):
            calls.append(
                ("create_event", kwargs)
            )

    session = FakeSession()
    service = SpecialistCalendarService(
        session=session,
        cabinets=FakeCabinets(),
        repository=FakeRepository(),
        event_repository=FakeEvents(),
    )

    action = await (
        service.delete_exception_for_user(
            user_id=user_id,
            tenant_id=tenant_id,
            language="uk",
            professional_cabinet_id=(
                cabinet_id
            ),
            exception_id=exception_id,
            platform="api",
        )
    )

    assert isinstance(
        action,
        SpecialistCalendarExceptionDeleteAction,
    )
    assert action.exception_id == exception_id

    assert calls == [
        (
            "require_cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "get_calendar",
            {
                "tenant_id": tenant_id,
                "specialist_id": (
                    specialist_id
                ),
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "delete_exception",
            {
                "tenant_id": tenant_id,
                "calendar_id": calendar_id,
                "exception_id": exception_id,
            },
        ),
        (
            "create_event",
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "event_type": (
                    "change_submitted"
                ),
                "entity_type": (
                    "professional_cabinet_"
                    "calendar_exception"
                ),
                "entity_id": exception_id,
                "payload": {
                    "professional_cabinet_id": (
                        str(cabinet_id)
                    ),
                    "operation": "deleted",
                },
                "platform": "api",
            },
        ),
    ]

    assert session.commits == 1
    assert session.rollbacks == 0



@pytest.mark.asyncio
async def test_specialist_calendar_exception_delete_uses_current_actor():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_calendar import (
        SpecialistCalendarExceptionDeleteAction,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    exception_id = uuid4()
    request_id = (
        "calendar-exception-delete"
    )
    calls = []

    api_actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.write",
        ),
    )

    service_actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    action = (
        SpecialistCalendarExceptionDeleteAction(
            actor=service_actor,
            exception_id=exception_id,
        )
    )

    class FakeCalendarService:
        async def delete_exception_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return action

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: api_actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: FakeCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.delete(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar/"
                "exceptions/"
                f"{exception_id}"
            ),
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200

    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "exception_id": exception_id,
            "platform": "api",
        }
    ]

    assert response.json() == {
        "data": {
            "id": str(exception_id),
            "deleted": True,
        },
        "meta": {},
        "request_id": request_id,
    }



def test_calendar_exceptions_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    collection_path = (
        "/api/v1/specialist/cabinets/"
        "{cabinet_id}/calendar/exceptions"
    )
    item_path = (
        "/api/v1/specialist/cabinets/"
        "{cabinet_id}/calendar/exceptions/"
        "{exception_id}"
    )

    collection = schema[
        "paths"
    ][collection_path]
    item = schema["paths"][item_path]

    assert set(collection) == {
        "get",
        "post",
    }
    assert set(item) == {
        "delete",
    }

    for operation in (
        collection["get"],
        collection["post"],
        item["delete"],
    ):
        assert operation["security"] == [
            {
                "BearerAuth": [],
            }
        ]

    get_response = (
        collection["get"]["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert get_response == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCalendarExceptionListResponse"
        )
    }

    post_request = (
        collection["post"]["requestBody"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert post_request == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCalendarExceptionCreateRequest"
        )
    }

    post_response = (
        collection["post"]["responses"]["201"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert post_response == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCalendarExceptionResponse"
        )
    }

    delete_response = (
        item["delete"]["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert delete_response == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCalendarExceptionDeleteResponse"
        )
    }



@pytest.mark.asyncio
async def test_calendar_repository_lists_confirmed_order_intervals():
    from datetime import (
        UTC,
        datetime,
    )
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.specialist_calendar import (
        SpecialistCalendarRepository,
    )

    tenant_id = uuid4()
    cabinet_id = uuid4()
    range_start = datetime(
        2026,
        9,
        1,
        tzinfo=UTC,
    )
    range_end = datetime(
        2026,
        9,
        8,
        tzinfo=UTC,
    )
    expected = [
        SimpleNamespace(
            start_at=datetime(
                2026,
                9,
                1,
                10,
                0,
                tzinfo=UTC,
            ),
            end_at=datetime(
                2026,
                9,
                1,
                11,
                0,
                tzinfo=UTC,
            ),
        )
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = SpecialistCalendarRepository(
        session
    )

    result = await (
        repository
        .list_confirmed_order_intervals(
            tenant_id=tenant_id,
            professional_cabinet_id=(
                cabinet_id
            ),
            range_start=range_start,
            range_end=range_end,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()
    normalized_sql = " ".join(sql.split())

    required = (
        "service_orders.tenant_id",
        (
            "service_orders."
            "professional_cabinet_id"
        ),
        (
            "service_orders.status = "
            "'confirmed'"
        ),
        (
            "service_orders.start_at "
            "is not null"
        ),
        (
            "service_orders.end_at "
            "is not null"
        ),
        (
            "service_orders.start_at <"
        ),
        (
            "service_orders.end_at >"
        ),
        "order by service_orders.start_at",
    )

    for value in required:
        assert value in normalized_sql



@pytest.mark.asyncio
async def test_calendar_repository_lists_exceptions_in_date_range():
    from datetime import date
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.specialist_calendar import (
        SpecialistCalendarRepository,
    )

    tenant_id = uuid4()
    calendar_id = uuid4()
    date_from = date(2026, 9, 1)
    date_to = date(2026, 9, 7)
    expected = [
        SimpleNamespace(id=uuid4()),
    ]

    class FakeScalars:
        def all(self):
            return expected

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = SpecialistCalendarRepository(
        session
    )

    result = await (
        repository.list_exceptions_in_range(
            tenant_id=tenant_id,
            calendar_id=calendar_id,
            date_from=date_from,
            date_to=date_to,
        )
    )

    assert result == expected
    assert len(session.statements) == 1

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    ).lower()
    normalized_sql = " ".join(sql.split())

    required = (
        (
            "professional_cabinet_calendar_"
            "exceptions.tenant_id"
        ),
        (
            "professional_cabinet_calendar_"
            "exceptions.calendar_id"
        ),
        (
            "professional_cabinet_calendar_"
            "exceptions.exception_date >="
        ),
        (
            "professional_cabinet_calendar_"
            "exceptions.exception_date <="
        ),
        "order by",
    )

    for value in required:
        assert value in normalized_sql



@pytest.mark.asyncio
async def test_calendar_slots_apply_schedule_exceptions_and_confirmed_orders():
    from datetime import (
        UTC,
        date,
        datetime,
        time,
    )
    from types import SimpleNamespace
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarSlotListAction,
        SpecialistCalendarService,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsAction,
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calendar_id = uuid4()
    calls = []

    date_from = date(2026, 9, 7)
    date_to = date(2026, 9, 7)

    actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            calls.append(
                ("require_cabinet", kwargs)
            )
            return SpecialistCabinetsAction(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeRepository:
        async def get_owned_calendar(
            self,
            **kwargs,
        ):
            calls.append(
                ("get_calendar", kwargs)
            )
            return SimpleNamespace(
                id=calendar_id,
                timezone="UTC",
                slot_duration_minutes=30,
                is_active=True,
            )

        async def list_active_work_intervals(
            self,
            **kwargs,
        ):
            calls.append(
                ("list_intervals", kwargs)
            )
            return [
                SimpleNamespace(
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(12, 0),
                )
            ]

        async def list_exceptions_in_range(
            self,
            **kwargs,
        ):
            calls.append(
                ("list_exceptions", kwargs)
            )
            return [
                SimpleNamespace(
                    exception_date=date_from,
                    exception_type=(
                        "unavailable"
                    ),
                    start_time=time(10, 0),
                    end_time=time(10, 30),
                )
            ]

        async def list_confirmed_order_intervals(
            self,
            **kwargs,
        ):
            calls.append(
                ("list_orders", kwargs)
            )
            return [
                SimpleNamespace(
                    start_at=datetime(
                        2026,
                        9,
                        7,
                        11,
                        0,
                        tzinfo=UTC,
                    ),
                    end_at=datetime(
                        2026,
                        9,
                        7,
                        11,
                        30,
                        tzinfo=UTC,
                    ),
                )
            ]

    service = SpecialistCalendarService(
        session=None,
        cabinets=FakeCabinets(),
        repository=FakeRepository(),
        event_repository=object(),
    )

    action = await service.list_slots_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        date_from=date_from,
        date_to=date_to,
    )

    assert isinstance(
        action,
        SpecialistCalendarSlotListAction,
    )

    assert calls == [
        (
            "require_cabinet",
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "get_calendar",
            {
                "tenant_id": tenant_id,
                "specialist_id": (
                    specialist_id
                ),
                "professional_cabinet_id": (
                    cabinet_id
                ),
            },
        ),
        (
            "list_intervals",
            {
                "tenant_id": tenant_id,
                "calendar_id": calendar_id,
            },
        ),
        (
            "list_exceptions",
            {
                "tenant_id": tenant_id,
                "calendar_id": calendar_id,
                "date_from": date_from,
                "date_to": date_to,
            },
        ),
        (
            "list_orders",
            {
                "tenant_id": tenant_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "range_start": datetime(
                    2026,
                    9,
                    7,
                    tzinfo=UTC,
                ),
                "range_end": datetime(
                    2026,
                    9,
                    8,
                    tzinfo=UTC,
                ),
            },
        ),
    ]

    actual = [
        (
            slot.start_at,
            slot.end_at,
        )
        for slot in action.result
    ]

    assert actual == [
        (
            datetime(
                2026,
                9,
                7,
                9,
                0,
                tzinfo=UTC,
            ),
            datetime(
                2026,
                9,
                7,
                9,
                30,
                tzinfo=UTC,
            ),
        ),
        (
            datetime(
                2026,
                9,
                7,
                9,
                30,
                tzinfo=UTC,
            ),
            datetime(
                2026,
                9,
                7,
                10,
                0,
                tzinfo=UTC,
            ),
        ),
        (
            datetime(
                2026,
                9,
                7,
                10,
                30,
                tzinfo=UTC,
            ),
            datetime(
                2026,
                9,
                7,
                11,
                0,
                tzinfo=UTC,
            ),
        ),
        (
            datetime(
                2026,
                9,
                7,
                11,
                30,
                tzinfo=UTC,
            ),
            datetime(
                2026,
                9,
                7,
                12,
                0,
                tzinfo=UTC,
            ),
        ),
    ]



@pytest.mark.asyncio
async def test_calendar_slots_use_cabinet_timezone_and_return_utc():
    from datetime import (
        UTC,
        date,
        datetime,
        time,
    )
    from types import SimpleNamespace
    from uuid import uuid4

    from services.specialist_calendar import (
        SpecialistCalendarService,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsAction,
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    calendar_id = uuid4()
    target_date = date(2026, 9, 7)

    actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    class FakeCabinets:
        async def require_owned_cabinet_for_user(
            self,
            **kwargs,
        ):
            return SpecialistCabinetsAction(
                actor=actor,
                result=SimpleNamespace(
                    id=cabinet_id,
                ),
            )

    class FakeRepository:
        async def get_owned_calendar(
            self,
            **kwargs,
        ):
            return SimpleNamespace(
                id=calendar_id,
                timezone="Europe/Kyiv",
                slot_duration_minutes=60,
                is_active=True,
            )

        async def list_active_work_intervals(
            self,
            **kwargs,
        ):
            return [
                SimpleNamespace(
                    weekday=1,
                    start_time=time(9, 0),
                    end_time=time(10, 0),
                )
            ]

        async def list_exceptions_in_range(
            self,
            **kwargs,
        ):
            return []

        async def list_confirmed_order_intervals(
            self,
            **kwargs,
        ):
            assert kwargs["range_start"] == (
                datetime(
                    2026,
                    9,
                    6,
                    21,
                    0,
                    tzinfo=UTC,
                )
            )
            assert kwargs["range_end"] == (
                datetime(
                    2026,
                    9,
                    7,
                    21,
                    0,
                    tzinfo=UTC,
                )
            )
            return []

    service = SpecialistCalendarService(
        session=None,
        cabinets=FakeCabinets(),
        repository=FakeRepository(),
        event_repository=object(),
    )

    action = await service.list_slots_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        date_from=target_date,
        date_to=target_date,
    )

    assert action.timezone == "Europe/Kyiv"
    assert len(action.result) == 1

    slot = action.result[0]
    assert slot.start_at == datetime(
        2026,
        9,
        7,
        6,
        0,
        tzinfo=UTC,
    )
    assert slot.end_at == datetime(
        2026,
        9,
        7,
        7,
        0,
        tzinfo=UTC,
    )



@pytest.mark.asyncio
async def test_specialist_calendar_slots_use_current_actor():
    from datetime import (
        UTC,
        date,
        datetime,
    )
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_calendar_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_calendar import (
        SpecialistCalendarSlotListAction,
        SpecialistCalendarSlotView,
    )
    from services.specialist_cabinets import (
        SpecialistCabinetsActor,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()
    request_id = "calendar-slots-request"
    date_from = date(2026, 9, 7)
    date_to = date(2026, 9, 7)
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="specialist",
        roles=("specialist",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=(
            "specialist.calendar.read",
        ),
    )

    service_actor = SpecialistCabinetsActor(
        user_id=user_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        language="uk",
    )

    action = SpecialistCalendarSlotListAction(
        actor=service_actor,
        timezone="Europe/Kyiv",
        result=(
            SpecialistCalendarSlotView(
                start_at=datetime(
                    2026,
                    9,
                    7,
                    6,
                    0,
                    tzinfo=UTC,
                ),
                end_at=datetime(
                    2026,
                    9,
                    7,
                    6,
                    30,
                    tzinfo=UTC,
                ),
            ),
        ),
    )

    class FakeCalendarService:
        async def list_slots_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return action

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_calendar_service
    ] = lambda: FakeCalendarService()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
        ),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            (
                "/api/v1/specialist/cabinets/"
                f"{cabinet_id}/calendar/slots"
            ),
            params={
                "from": "2026-09-07",
                "to": "2026-09-07",
            },
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 200

    assert calls == [
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "language": "uk",
            "professional_cabinet_id": (
                cabinet_id
            ),
            "date_from": date_from,
            "date_to": date_to,
        }
    ]

    assert response.json() == {
        "data": {
            "timezone": "Europe/Kyiv",
            "items": [
                {
                    "start_at": (
                        "2026-09-07T06:00:00Z"
                    ),
                    "end_at": (
                        "2026-09-07T06:30:00Z"
                    ),
                }
            ],
        },
        "meta": {},
        "request_id": request_id,
    }



def test_calendar_slots_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()

    operation = schema["paths"][
        (
            "/api/v1/specialist/cabinets/"
            "{cabinet_id}/calendar/slots"
        )
    ]["get"]

    assert operation["security"] == [
        {
            "BearerAuth": [],
        }
    ]

    query_parameters = {
        parameter["name"]: parameter
        for parameter
        in operation["parameters"]
        if parameter["in"] == "query"
    }

    assert set(query_parameters) == {
        "from",
        "to",
    }
    assert (
        query_parameters["from"]["required"]
        is True
    )
    assert (
        query_parameters["to"]["required"]
        is True
    )
    assert (
        query_parameters["from"]["schema"][
            "format"
        ]
        == "date"
    )
    assert (
        query_parameters["to"]["schema"][
            "format"
        ]
        == "date"
    )

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistCalendarSlotListResponse"
        )
    }

    components = schema[
        "components"
    ]["schemas"]

    assert set(
        components[
            "SpecialistCalendarSlotData"
        ]["properties"]
    ) == {
        "timezone",
        "items",
    }

    assert set(
        components[
            "SpecialistCalendarSlotItem"
        ]["properties"]
    ) == {
        "start_at",
        "end_at",
    }
