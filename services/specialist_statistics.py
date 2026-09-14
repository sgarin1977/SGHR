from dataclasses import dataclass
from datetime import (
    UTC,
    date,
    datetime,
    time,
    timedelta,
)
from uuid import UUID
from zoneinfo import (
    ZoneInfo,
    ZoneInfoNotFoundError,
)

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.specialist_calendar import (
    SpecialistCalendarRepository,
)
from database.repositories.specialist_statistics import (
    SpecialistStatisticsRepository,
)
from services.specialist_cabinets import (
    SpecialistCabinetsActor,
    SpecialistCabinetsService,
)


class SpecialistStatisticsAccessError(
    PermissionError
):
    pass


class SpecialistStatisticsNotFoundError(
    SpecialistStatisticsAccessError
):
    pass


class SpecialistStatisticsValidationError(
    ValueError
):
    pass


@dataclass(frozen=True)
class SpecialistStatisticsView:
    professional_cabinet_id: UUID
    period: str | None
    date_from: date
    date_to: date
    timezone: str
    requests: int
    unique_clients: int
    started_dialogs: int
    completed_dialogs: int
    orders: int
    completed_orders: int
    published_reviews: int
    average_published_rating: float | None
    request_to_dialog: float
    dialog_to_order: float
    order_to_completed: float


@dataclass(frozen=True)
class SpecialistStatisticsAction:
    actor: SpecialistCabinetsActor
    result: SpecialistStatisticsView


def _conversion(
    numerator: int,
    denominator: int,
) -> float:
    if denominator == 0:
        return 0.0

    return numerator / denominator


class SpecialistStatisticsService:
    PERIOD_DAYS = {
        "7d": 7,
        "30d": 30,
        "90d": 90,
    }

    def __init__(
        self,
        session: AsyncSession,
        *,
        cabinets: (
            SpecialistCabinetsService | None
        ) = None,
        calendar_repository: (
            SpecialistCalendarRepository | None
        ) = None,
        repository: (
            SpecialistStatisticsRepository | None
        ) = None,
    ):
        self.session = session
        self.cabinets = (
            cabinets
            or SpecialistCabinetsService(session)
        )
        self.calendar_repository = (
            calendar_repository
            or SpecialistCalendarRepository(
                session
            )
        )
        self.repository = (
            repository
            or SpecialistStatisticsRepository(
                session
            )
        )

    @classmethod
    def _resolve_dates(
        cls,
        *,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        today: date,
    ) -> tuple[str | None, date, date]:
        if period is not None:
            if (
                period not in cls.PERIOD_DAYS
                or date_from is not None
                or date_to is not None
            ):
                raise (
                    SpecialistStatisticsValidationError(
                        "Statistics period is not valid."
                    )
                )

            days = cls.PERIOD_DAYS[period]
            return (
                period,
                today - timedelta(days=days - 1),
                today,
            )

        if (
            date_from is None
            or date_to is None
            or isinstance(date_from, datetime)
            or isinstance(date_to, datetime)
            or date_from > date_to
        ):
            raise SpecialistStatisticsValidationError(
                "Statistics date range is not valid."
            )

        return None, date_from, date_to

    async def get_statistics_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        now: datetime | None = None,
    ) -> SpecialistStatisticsAction:
        current_time = now or datetime.now(UTC)

        if (
            current_time.tzinfo is None
            or current_time.utcoffset() is None
        ):
            raise SpecialistStatisticsValidationError(
                "Current time must be timezone-aware."
            )

        cabinet_action = await (
            self.cabinets
            .require_owned_cabinet_for_user(
                user_id=user_id,
                tenant_id=tenant_id,
                language=language,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )
        actor = cabinet_action.actor

        calendar = await (
            self.calendar_repository
            .get_owned_calendar(
                tenant_id=actor.tenant_id,
                specialist_id=(
                    actor.specialist_id
                ),
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
            )
        )

        if calendar is None:
            raise SpecialistStatisticsNotFoundError(
                "Professional cabinet calendar "
                "was not found."
            )

        try:
            cabinet_timezone = ZoneInfo(
                calendar.timezone
            )
        except (
            ZoneInfoNotFoundError,
            ValueError,
        ):
            raise SpecialistStatisticsValidationError(
                "Professional cabinet timezone "
                "is not valid."
            ) from None

        today = current_time.astimezone(
            cabinet_timezone
        ).date()

        (
            normalized_period,
            normalized_date_from,
            normalized_date_to,
        ) = self._resolve_dates(
            period=period,
            date_from=date_from,
            date_to=date_to,
            today=today,
        )

        local_start = datetime.combine(
            normalized_date_from,
            time.min,
            tzinfo=cabinet_timezone,
        )
        local_end = datetime.combine(
            normalized_date_to
            + timedelta(days=1),
            time.min,
            tzinfo=cabinet_timezone,
        )

        metrics = await (
            self.repository.get_period_metrics(
                tenant_id=actor.tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                start_at=local_start.astimezone(
                    UTC
                ),
                end_at=local_end.astimezone(
                    UTC
                ),
            )
        )

        requests = int(
            metrics.get("requests") or 0
        )
        unique_clients = int(
            metrics.get("unique_clients") or 0
        )
        started_dialogs = int(
            metrics.get("started_dialogs") or 0
        )
        completed_dialogs = int(
            metrics.get("completed_dialogs") or 0
        )
        orders = int(
            metrics.get("orders") or 0
        )
        completed_orders = int(
            metrics.get("completed_orders") or 0
        )
        published_reviews = int(
            metrics.get("published_reviews") or 0
        )
        average_rating = metrics.get(
            "average_published_rating"
        )

        view = SpecialistStatisticsView(
            professional_cabinet_id=(
                professional_cabinet_id
            ),
            period=normalized_period,
            date_from=normalized_date_from,
            date_to=normalized_date_to,
            timezone=calendar.timezone,
            requests=requests,
            unique_clients=unique_clients,
            started_dialogs=started_dialogs,
            completed_dialogs=completed_dialogs,
            orders=orders,
            completed_orders=completed_orders,
            published_reviews=published_reviews,
            average_published_rating=(
                float(average_rating)
                if average_rating is not None
                else None
            ),
            request_to_dialog=_conversion(
                started_dialogs,
                requests,
            ),
            dialog_to_order=_conversion(
                orders,
                started_dialogs,
            ),
            order_to_completed=_conversion(
                completed_orders,
                orders,
            ),
        )

        return SpecialistStatisticsAction(
            actor=actor,
            result=view,
        )
