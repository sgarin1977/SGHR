from sqlalchemy import DateTime


def test_statistics_timestamp_models_match_database():
    from database.models import (
        ConversationThread,
        Review,
    )

    completed_at = (
        ConversationThread
        .__table__.columns["completed_at"]
    )
    published_at = (
        Review
        .__table__.columns["published_at"]
    )

    assert isinstance(
        completed_at.type,
        DateTime,
    )
    assert completed_at.type.timezone is True
    assert completed_at.nullable is True

    assert isinstance(
        published_at.type,
        DateTime,
    )
    assert published_at.type.timezone is True
    assert published_at.nullable is True



import pytest


@pytest.mark.asyncio
async def test_complete_thread_sets_timezone_aware_completed_at():
    from datetime import UTC
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from database.models import (
        ContactRequest,
        Specialist,
    )
    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    thread_id = uuid4()
    request_id = uuid4()
    client_user_id = uuid4()
    specialist_user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()

    thread = SimpleNamespace(
        id=thread_id,
        tenant_id=tenant_id,
        specialist_id=specialist_id,
        professional_cabinet_id=cabinet_id,
        client_user_id=client_user_id,
        context_type="contact_request",
        context_id=request_id,
        status="in_discussion",
        updated_at=None,
    )
    specialist = SimpleNamespace(
        id=specialist_id,
        user_id=specialist_user_id,
        active_professional_cabinet_id=(
            cabinet_id
        ),
    )
    contact_request = SimpleNamespace(
        id=request_id,
        tenant_id=tenant_id,
        status="accepted",
        updated_at=None,
        extra_metadata={
            "completion_requested_by_user_id": (
                str(client_user_id)
            ),
        },
    )

    class FakeSession:
        def __init__(self):
            self.added = []
            self.commits = 0

        async def get(self, model, value):
            if model is Specialist:
                assert value == specialist_id
                return specialist

            if model is ContactRequest:
                assert value == request_id
                return contact_request

            raise AssertionError(
                f"Unexpected model: {model}"
            )

        def add_all(self, rows):
            self.added.extend(rows)

        async def commit(self):
            self.commits += 1

    session = FakeSession()
    repository = ContactChatRepository(
        session
    )
    repository.get_thread_for_user = (
        AsyncMock(
            return_value=thread,
        )
    )

    result = await repository.complete_thread(
        tenant_id=tenant_id,
        thread_id=thread_id,
        actor_user_id=specialist_user_id,
        platform="api",
    )

    assert result is thread
    assert thread.status == "completed"
    assert thread.completed_at is not None
    assert thread.completed_at.tzinfo is UTC
    assert (
        thread.updated_at
        == thread.completed_at
    )
    assert session.commits == 1



@pytest.mark.asyncio
async def test_publish_review_sets_timezone_aware_published_at():
    from datetime import UTC
    from types import SimpleNamespace
    from uuid import uuid4

    from database.models import Review
    from database.repositories.reviews import (
        ReviewRepository,
    )

    review_id = uuid4()
    review = SimpleNamespace(
        id=review_id,
        status="pending_moderation",
        updated_at=None,
    )

    class FakeSession:
        def __init__(self):
            self.flushes = 0

        async def get(self, model, value):
            assert model is Review
            assert value == review_id
            return review

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = ReviewRepository(session)

    result = await repository.publish_review(
        review_id=review_id,
    )

    assert result is review
    assert review.status == "published"
    assert review.published_at is not None
    assert review.published_at.tzinfo is UTC
    assert (
        review.updated_at
        == review.published_at
    )
    assert session.flushes == 1



@pytest.mark.asyncio
async def test_review_moderation_publish_sets_published_at():
    from datetime import UTC
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy import true

    from database.repositories.reviews import (
        ReviewRepository,
    )

    tenant_id = uuid4()
    moderator_id = uuid4()
    review_id = uuid4()

    review = SimpleNamespace(
        id=review_id,
        status="pending_moderation",
        updated_at=None,
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return review

    class FakeSession:
        def __init__(self):
            self.flushes = 0
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

        async def flush(self):
            self.flushes += 1

    class TestReviewRepository(
        ReviewRepository
    ):
        async def require_moderator(
            self,
            **kwargs,
        ):
            return {"moderator"}

        async def _moderation_scope_predicate(
            self,
            **kwargs,
        ):
            return true()

    session = FakeSession()
    repository = TestReviewRepository(
        session
    )

    result, before_status = await (
        repository.set_review_status(
            tenant_id=tenant_id,
            moderator_user_id=moderator_id,
            review_id=review_id,
            status="published",
        )
    )

    assert result is review
    assert before_status == (
        "pending_moderation"
    )
    assert review.status == "published"
    assert review.published_at is not None
    assert review.published_at.tzinfo is UTC
    assert (
        review.updated_at
        == review.published_at
    )
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_admin_completion_sets_thread_completed_at():
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from database.repositories.contact import (
        ContactChatRepository,
    )

    completed_at = datetime(
        2026,
        9,
        1,
        12,
        0,
        tzinfo=UTC,
    )
    tenant_id = uuid4()

    thread = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        status="in_discussion",
        updated_at=None,
    )
    contact_request = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        status="accepted",
        updated_at=None,
        extra_metadata={},
    )

    class FakeSession:
        def __init__(self):
            self.events = []
            self.flushes = 0

        def add_all(self, values):
            self.events.extend(values)

        async def flush(self):
            self.flushes += 1

    session = FakeSession()
    repository = ContactChatRepository(session)

    await repository.complete_contact_request_by_admin(
        contact_request=contact_request,
        thread=thread,
        admin_user_id=uuid4(),
        reason="Confirmed by support.",
        completed_at=completed_at,
        platform="api",
    )

    assert thread.status == "completed"
    assert thread.completed_at == completed_at
    assert thread.completed_at.tzinfo is UTC
    assert session.flushes == 1


def test_support_completion_uses_timezone_aware_utc():
    import ast
    from pathlib import Path

    support_source = Path(
        "services/support.py"
    ).read_text(encoding="utf-8-sig")
    tree = ast.parse(support_source)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr
        == "complete_contact_request_by_admin"
    ]
    assert len(calls) == 1

    arguments = {
        keyword.arg: keyword.value
        for keyword in calls[0].keywords
    }
    completed_at = arguments["completed_at"]

    assert isinstance(completed_at, ast.Call)
    assert isinstance(
        completed_at.func,
        ast.Attribute,
    )
    assert isinstance(
        completed_at.func.value,
        ast.Name,
    )
    assert completed_at.func.value.id == "datetime"
    assert completed_at.func.attr == "now"
    assert len(completed_at.args) == 1
    assert isinstance(completed_at.args[0], ast.Name)
    assert completed_at.args[0].id == "UTC"


def test_service_order_completion_timestamp_is_timezone_aware():
    from database.models import ServiceOrder

    column = (
        ServiceOrder.__table__
        .columns["completed_at"]
    )

    assert column.nullable is True
    assert column.type.timezone is True


@pytest.mark.asyncio
async def test_complete_service_order_sets_timezone_aware_completed_at():
    from datetime import UTC
    from types import SimpleNamespace
    from uuid import uuid4

    from database.repositories.contact import (
        ContactChatRepository,
    )

    tenant_id = uuid4()
    client_user_id = uuid4()

    order = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        status="confirmed",
        client_user_id=client_user_id,
        specialist_user_id=uuid4(),
        specialist_id=uuid4(),
        professional_cabinet_id=uuid4(),
        thread_id=uuid4(),
        contact_request_id=uuid4(),
        completed_by=None,
        completed_at=None,
        updated_at=None,
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return order

    class FakeSession:
        def __init__(self):
            self.events = []
            self.commits = 0
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

        def add(self, value):
            self.events.append(value)

        async def commit(self):
            self.commits += 1

    session = FakeSession()
    repository = ContactChatRepository(session)

    result = await repository.complete_service_order(
        order_id=order.id,
        actor_user_id=client_user_id,
        tenant_id=tenant_id,
        platform="api",
    )

    assert result is order
    assert order.status == "completed"
    assert order.completed_at is not None
    assert order.completed_at.tzinfo is UTC
    assert order.updated_at == order.completed_at
    assert session.commits == 1


def test_statistics_source_timestamps_match_database():
    from database.models import (
        ContactRequest,
        ConversationThread,
        Review,
        ServiceOrder,
    )

    timestamp_columns = (
        ContactRequest.__table__.columns[
            "created_at"
        ],
        ConversationThread.__table__.columns[
            "created_at"
        ],
        ConversationThread.__table__.columns[
            "completed_at"
        ],
        ServiceOrder.__table__.columns[
            "created_at"
        ],
        ServiceOrder.__table__.columns[
            "completed_at"
        ],
        Review.__table__.columns[
            "published_at"
        ],
    )

    for column in timestamp_columns:
        assert column.type.timezone is True, (
            f"{column.table.name}.{column.name} "
            "must be timezone-aware"
        )


@pytest.mark.asyncio
async def test_statistics_repository_aggregates_scoped_period():
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from database.repositories.specialist_statistics import (
        SpecialistStatisticsRepository,
    )

    tenant_id = uuid4()
    cabinet_id = uuid4()
    start_at = datetime(
        2026,
        8,
        26,
        tzinfo=UTC,
    )
    end_at = datetime(
        2026,
        9,
        2,
        tzinfo=UTC,
    )

    expected = {
        "requests": 10,
        "unique_clients": 7,
        "started_dialogs": 8,
        "completed_dialogs": 6,
        "orders": 5,
        "completed_orders": 4,
        "published_reviews": 3,
        "average_published_rating": Decimal(
            "4.50"
        ),
    }

    class FakeMappings:
        def one(self):
            return expected

    class FakeResult:
        def mappings(self):
            return FakeMappings()

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return FakeResult()

    session = FakeSession()
    repository = SpecialistStatisticsRepository(
        session
    )

    result = await repository.get_period_metrics(
        tenant_id=tenant_id,
        professional_cabinet_id=cabinet_id,
        start_at=start_at,
        end_at=end_at,
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
    sql = " ".join(sql.split())

    scoped_tables = (
        "contact_requests",
        "conversation_threads",
        "service_orders",
        "reviews",
    )

    for table in scoped_tables:
        assert f"{table}.tenant_id =" in sql
        assert (
            f"{table}.professional_cabinet_id ="
            in sql
        )

    required_timestamps = (
        "contact_requests.created_at",
        "conversation_threads.created_at",
        "conversation_threads.completed_at",
        "service_orders.created_at",
        "service_orders.completed_at",
        "reviews.published_at",
    )

    for timestamp in required_timestamps:
        assert f"{timestamp} >=" in sql
        assert f"{timestamp} <" in sql

    assert "distinct" in sql
    assert "contact_requests.from_user_id" in sql
    assert "reviews.status = 'published'" in sql


@pytest.mark.asyncio
async def test_statistics_service_calculates_calendar_period_and_conversions():
    from datetime import UTC, datetime
    from decimal import Decimal
    from types import SimpleNamespace
    from uuid import uuid4

    from services.specialist_cabinets import (
        SpecialistCabinetsActor,
    )
    from services.specialist_statistics import (
        SpecialistStatisticsService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    specialist_id = uuid4()
    cabinet_id = uuid4()

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
            assert kwargs == {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "language": "uk",
                "professional_cabinet_id": (
                    cabinet_id
                ),
            }
            return SimpleNamespace(actor=actor)

    class FakeCalendarRepository:
        async def get_owned_calendar(
            self,
            **kwargs,
        ):
            assert kwargs == {
                "tenant_id": tenant_id,
                "specialist_id": specialist_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
            }
            return SimpleNamespace(
                id=uuid4(),
                timezone="Europe/Kyiv",
            )

    class FakeStatisticsRepository:
        async def get_period_metrics(
            self,
            **kwargs,
        ):
            assert kwargs == {
                "tenant_id": tenant_id,
                "professional_cabinet_id": (
                    cabinet_id
                ),
                "start_at": datetime(
                    2026,
                    8,
                    25,
                    21,
                    0,
                    tzinfo=UTC,
                ),
                "end_at": datetime(
                    2026,
                    9,
                    1,
                    21,
                    0,
                    tzinfo=UTC,
                ),
            }
            return {
                "requests": 10,
                "unique_clients": 7,
                "started_dialogs": 8,
                "completed_dialogs": 6,
                "orders": 5,
                "completed_orders": 4,
                "published_reviews": 3,
                "average_published_rating": (
                    Decimal("4.50")
                ),
            }

    service = SpecialistStatisticsService(
        session=None,
        cabinets=FakeCabinets(),
        calendar_repository=(
            FakeCalendarRepository()
        ),
        repository=(
            FakeStatisticsRepository()
        ),
    )

    action = await service.get_statistics_for_user(
        user_id=user_id,
        tenant_id=tenant_id,
        language="uk",
        professional_cabinet_id=cabinet_id,
        period="7d",
        date_from=None,
        date_to=None,
        now=datetime(
            2026,
            9,
            1,
            12,
            0,
            tzinfo=UTC,
        ),
    )

    assert action.actor is actor
    assert action.result.period == "7d"
    assert action.result.date_from.isoformat() == (
        "2026-08-26"
    )
    assert action.result.date_to.isoformat() == (
        "2026-09-01"
    )
    assert action.result.timezone == "Europe/Kyiv"

    assert action.result.requests == 10
    assert action.result.unique_clients == 7
    assert action.result.started_dialogs == 8
    assert action.result.completed_dialogs == 6
    assert action.result.orders == 5
    assert action.result.completed_orders == 4
    assert action.result.published_reviews == 3
    assert (
        action.result.average_published_rating
        == 4.5
    )

    assert (
        action.result.request_to_dialog
        == 0.8
    )
    assert (
        action.result.dialog_to_order
        == 0.625
    )
    assert (
        action.result.order_to_completed
        == 0.8
    )


def test_statistics_custom_dates_and_zero_denominator_contract():
    from datetime import date

    from services.specialist_statistics import (
        SpecialistStatisticsService,
        _conversion,
    )

    period, date_from, date_to = (
        SpecialistStatisticsService
        ._resolve_dates(
            period=None,
            date_from=date(
                2026,
                8,
                1,
            ),
            date_to=date(
                2026,
                8,
                31,
            ),
            today=date(
                2026,
                9,
                1,
            ),
        )
    )

    assert period is None
    assert date_from == date(
        2026,
        8,
        1,
    )
    assert date_to == date(
        2026,
        8,
        31,
    )

    assert _conversion(0, 0) == 0.0
    assert _conversion(4, 0) == 0.0


@pytest.mark.asyncio
async def test_specialist_statistics_endpoint_uses_current_actor():
    from datetime import date
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_statistics_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_statistics import (
        SpecialistStatisticsView,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
    request_id = "statistics-request"
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
            "specialist.statistics.read",
        ),
    )

    result = SpecialistStatisticsView(
        professional_cabinet_id=cabinet_id,
        period="7d",
        date_from=date(2026, 8, 26),
        date_to=date(2026, 9, 1),
        timezone="Europe/Kyiv",
        requests=10,
        unique_clients=7,
        started_dialogs=8,
        completed_dialogs=6,
        orders=5,
        completed_orders=4,
        published_reviews=3,
        average_published_rating=4.5,
        request_to_dialog=0.8,
        dialog_to_order=0.625,
        order_to_completed=0.8,
    )

    class FakeStatisticsService:
        async def get_statistics_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                actor=object(),
                result=result,
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_statistics_service
    ] = lambda: FakeStatisticsService()

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
                f"{cabinet_id}/statistics"
            ),
            params={
                "period": "7d",
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
            "period": "7d",
            "date_from": None,
            "date_to": None,
        }
    ]

    assert response.json() == {
        "data": {
            "professional_cabinet_id": (
                str(cabinet_id)
            ),
            "period": "7d",
            "date_from": "2026-08-26",
            "date_to": "2026-09-01",
            "timezone": "Europe/Kyiv",
            "requests": 10,
            "unique_clients": 7,
            "started_dialogs": 8,
            "completed_dialogs": 6,
            "orders": 5,
            "completed_orders": 4,
            "published_reviews": 3,
            "average_published_rating": 4.5,
            "request_to_dialog": 0.8,
            "dialog_to_order": 0.625,
            "order_to_completed": 0.8,
        },
        "meta": {},
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_specialist_statistics_requires_read_permission():
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_statistics_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )

    cabinet_id = uuid4()
    request_id = "statistics-permission"

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

    class ForbiddenService:
        async def get_statistics_for_user(
            self,
            **kwargs,
        ):
            raise AssertionError(
                "Statistics service must not be "
                "called without permission."
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_statistics_service
    ] = lambda: ForbiddenService()

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
                f"{cabinet_id}/statistics"
            ),
            params={
                "period": "7d",
            },
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
async def test_specialist_statistics_requires_authentication():
    from uuid import uuid4

    import httpx

    from api.app import create_app

    cabinet_id = uuid4()
    request_id = "statistics-authentication"

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
                f"{cabinet_id}/statistics"
            ),
            params={
                "period": "7d",
            },
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required.",
            "request_id": request_id,
        },
    }


@pytest.mark.asyncio
async def test_specialist_statistics_endpoint_accepts_custom_dates():
    from datetime import date
    from types import SimpleNamespace
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_statistics_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_statistics import (
        SpecialistStatisticsView,
    )

    user_id = uuid4()
    tenant_id = uuid4()
    cabinet_id = uuid4()
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
            "specialist.statistics.read",
        ),
    )

    class FakeStatisticsService:
        async def get_statistics_for_user(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return SimpleNamespace(
                actor=object(),
                result=SpecialistStatisticsView(
                    professional_cabinet_id=(
                        cabinet_id
                    ),
                    period=None,
                    date_from=date(
                        2026,
                        8,
                        1,
                    ),
                    date_to=date(
                        2026,
                        8,
                        31,
                    ),
                    timezone="Europe/Kyiv",
                    requests=0,
                    unique_clients=0,
                    started_dialogs=0,
                    completed_dialogs=0,
                    orders=0,
                    completed_orders=0,
                    published_reviews=0,
                    average_published_rating=None,
                    request_to_dialog=0.0,
                    dialog_to_order=0.0,
                    order_to_completed=0.0,
                ),
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_statistics_service
    ] = lambda: FakeStatisticsService()

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
                f"{cabinet_id}/statistics"
            ),
            params={
                "date_from": "2026-08-01",
                "date_to": "2026-08-31",
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
            "period": None,
            "date_from": date(
                2026,
                8,
                1,
            ),
            "date_to": date(
                2026,
                8,
                31,
            ),
        }
    ]

    data = response.json()["data"]
    assert data["period"] is None
    assert data["date_from"] == "2026-08-01"
    assert data["date_to"] == "2026-08-31"
    assert data["request_to_dialog"] == 0.0
    assert data["dialog_to_order"] == 0.0
    assert data["order_to_completed"] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "service_error",
        "expected_status",
        "expected_code",
        "expected_message",
    ),
    (
        (
            "not_found",
            404,
            "cabinet_not_found",
            (
                "Professional cabinet "
                "was not found."
            ),
        ),
        (
            "validation",
            422,
            "statistics_validation_error",
            (
                "Statistics period "
                "is not valid."
            ),
        ),
    ),
)
async def test_specialist_statistics_hides_service_errors(
    service_error,
    expected_status,
    expected_code,
    expected_message,
):
    from uuid import uuid4

    import httpx

    from api.app import create_app
    from api.auth import get_current_actor
    from api.dependencies import (
        get_specialist_statistics_service,
    )
    from services.api_identity import (
        ApiActorContext,
    )
    from services.specialist_statistics import (
        SpecialistStatisticsNotFoundError,
        SpecialistStatisticsValidationError,
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
            "specialist.statistics.read",
        ),
    )

    class FailingStatisticsService:
        async def get_statistics_for_user(
            self,
            **kwargs,
        ):
            if service_error == "not_found":
                raise (
                    SpecialistStatisticsNotFoundError(
                        "Private cabinet details."
                    )
                )

            raise (
                SpecialistStatisticsValidationError(
                    "Private validation details."
                )
            )

    application = create_app()
    application.dependency_overrides[
        get_current_actor
    ] = lambda: actor
    application.dependency_overrides[
        get_specialist_statistics_service
    ] = lambda: FailingStatisticsService()

    request_id = (
        f"statistics-{service_error}"
    )

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
                f"{uuid4()}/statistics"
            ),
            params={
                "period": "7d",
            },
            headers={
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == (
        expected_status
    )
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": expected_message,
            "request_id": request_id,
        },
    }
    assert "Private" not in response.text


def test_specialist_statistics_openapi_contract():
    from api.app import create_app

    schema = create_app().openapi()
    path = (
        "/api/v1/specialist/cabinets/"
        "{cabinet_id}/statistics"
    )

    operation = schema["paths"][path]["get"]

    response_schema = (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]
    )
    assert response_schema == {
        "$ref": (
            "#/components/schemas/"
            "SpecialistStatisticsResponse"
        )
    }

    parameters = {
        (
            parameter["name"],
            parameter["in"],
        )
        for parameter in operation[
            "parameters"
        ]
    }

    assert parameters == {
        ("cabinet_id", "path"),
        ("period", "query"),
        ("date_from", "query"),
        ("date_to", "query"),
    }

    assert ("user_id", "query") not in parameters
    assert ("tenant_id", "query") not in parameters

    components = schema[
        "components"
    ]["schemas"]

    data_fields = set(
        components[
            "SpecialistStatisticsData"
        ]["properties"]
    )

    assert data_fields == {
        "professional_cabinet_id",
        "period",
        "date_from",
        "date_to",
        "timezone",
        "requests",
        "unique_clients",
        "started_dialogs",
        "completed_dialogs",
        "orders",
        "completed_orders",
        "published_reviews",
        "average_published_rating",
        "request_to_dialog",
        "dialog_to_order",
        "order_to_completed",
    }
