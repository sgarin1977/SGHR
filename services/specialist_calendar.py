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

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.repositories.event import (
    EventRepository,
)
from database.repositories.specialist_calendar import (
    SpecialistCalendarRepository,
)
from services.specialist_cabinets import (
    SpecialistCabinetsActor,
    SpecialistCabinetsService,
)


class SpecialistCalendarAccessError(
    PermissionError
):
    pass


class SpecialistCalendarNotFoundError(
    SpecialistCalendarAccessError
):
    pass


class SpecialistCalendarValidationError(
    ValueError
):
    pass


class SpecialistCalendarExceptionNotFoundError(
    SpecialistCalendarNotFoundError
):
    pass


@dataclass(frozen=True)
class SpecialistCalendarScheduleInterval:
    weekday: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class SpecialistCalendarWorkIntervalView:
    id: UUID
    weekday: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class SpecialistCalendarView:
    professional_cabinet_id: UUID
    timezone: str
    slot_duration_minutes: int
    is_active: bool
    work_intervals: tuple[
        SpecialistCalendarWorkIntervalView,
        ...,
    ]


@dataclass(frozen=True)
class SpecialistCalendarExceptionView:
    id: UUID
    exception_date: date
    exception_type: str
    start_time: time | None
    end_time: time | None
    reason: str | None


@dataclass(frozen=True)
class SpecialistCalendarExceptionListAction:
    actor: SpecialistCabinetsActor
    result: tuple[
        SpecialistCalendarExceptionView,
        ...,
    ]


@dataclass(frozen=True)
class SpecialistCalendarExceptionAction:
    actor: SpecialistCabinetsActor
    result: SpecialistCalendarExceptionView


@dataclass(frozen=True)
class SpecialistCalendarExceptionDeleteAction:
    actor: SpecialistCabinetsActor
    exception_id: UUID


@dataclass(frozen=True)
class SpecialistCalendarSlotView:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class SpecialistCalendarSlotListAction:
    actor: SpecialistCabinetsActor
    timezone: str
    result: tuple[
        SpecialistCalendarSlotView,
        ...,
    ]


@dataclass(frozen=True)
class SpecialistCalendarAction:
    actor: SpecialistCabinetsActor
    result: SpecialistCalendarView



def _merge_calendar_intervals(
    intervals: list[
        tuple[datetime, datetime]
    ],
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []

    ordered = sorted(
        intervals,
        key=lambda item: (
            item[0],
            item[1],
        ),
    )
    merged = [ordered[0]]

    for start_at, end_at in ordered[1:]:
        previous_start, previous_end = (
            merged[-1]
        )

        if start_at <= previous_end:
            merged[-1] = (
                previous_start,
                max(previous_end, end_at),
            )
        else:
            merged.append(
                (start_at, end_at)
            )

    return merged


def _subtract_calendar_interval(
    intervals: list[
        tuple[datetime, datetime]
    ],
    blocked_start: datetime,
    blocked_end: datetime,
) -> list[tuple[datetime, datetime]]:
    result: list[
        tuple[datetime, datetime]
    ] = []

    for start_at, end_at in intervals:
        if (
            blocked_end <= start_at
            or blocked_start >= end_at
        ):
            result.append(
                (start_at, end_at)
            )
            continue

        if blocked_start > start_at:
            result.append(
                (
                    start_at,
                    min(
                        blocked_start,
                        end_at,
                    ),
                )
            )

        if blocked_end < end_at:
            result.append(
                (
                    max(
                        blocked_end,
                        start_at,
                    ),
                    end_at,
                )
            )

    return result


class SpecialistCalendarService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cabinets: (
            SpecialistCabinetsService | None
        ) = None,
        repository: (
            SpecialistCalendarRepository | None
        ) = None,
        event_repository: (
            EventRepository | None
        ) = None,
    ):
        self.session = session
        self.cabinets = (
            cabinets
            or SpecialistCabinetsService(
                session
            )
        )
        self.repository = (
            repository
            or SpecialistCalendarRepository(
                session
            )
        )
        self.event_repository = (
            event_repository
            or EventRepository(session)
        )

    async def get_calendar_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
    ) -> SpecialistCalendarAction:
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
            self.repository.get_owned_calendar(
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
            raise (
                SpecialistCalendarNotFoundError(
                    "Professional cabinet "
                    "calendar was not found."
                )
            )

        rows = await (
            self.repository
            .list_active_work_intervals(
                tenant_id=actor.tenant_id,
                calendar_id=calendar.id,
            )
        )

        view = SpecialistCalendarView(
            professional_cabinet_id=(
                professional_cabinet_id
            ),
            timezone=calendar.timezone,
            slot_duration_minutes=(
                calendar.slot_duration_minutes
            ),
            is_active=calendar.is_active,
            work_intervals=tuple(
                SpecialistCalendarWorkIntervalView(
                    id=row.id,
                    weekday=row.weekday,
                    start_time=row.start_time,
                    end_time=row.end_time,
                )
                for row in rows
            ),
        )

        return SpecialistCalendarAction(
            actor=actor,
            result=view,
        )

    async def update_schedule_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        timezone: str,
        slot_duration_minutes: int,
        work_intervals: tuple[
            SpecialistCalendarScheduleInterval,
            ...,
        ],
        platform: str = "telegram",
    ) -> SpecialistCalendarAction:
        normalized_timezone = (
            timezone.strip()
            if isinstance(timezone, str)
            else ""
        )

        try:
            ZoneInfo(normalized_timezone)
        except (
            ZoneInfoNotFoundError,
            ValueError,
        ):
            raise (
                SpecialistCalendarValidationError(
                    "Calendar timezone "
                    "is not valid."
                )
            ) from None

        if (
            not isinstance(
                slot_duration_minutes,
                int,
            )
            or isinstance(
                slot_duration_minutes,
                bool,
            )
            or slot_duration_minutes <= 0
        ):
            raise (
                SpecialistCalendarValidationError(
                    "Calendar slot duration "
                    "is not valid."
                )
            )

        intervals_by_weekday: dict[
            int,
            list[
                SpecialistCalendarScheduleInterval
            ],
        ] = {}

        for interval in work_intervals:
            if (
                interval.weekday < 1
                or interval.weekday > 7
                or interval.start_time
                >= interval.end_time
            ):
                raise (
                    SpecialistCalendarValidationError(
                        "Calendar work interval "
                        "is not valid."
                    )
                )

            intervals_by_weekday.setdefault(
                interval.weekday,
                [],
            ).append(interval)

        for day_intervals in (
            intervals_by_weekday.values()
        ):
            ordered = sorted(
                day_intervals,
                key=lambda item: (
                    item.start_time,
                    item.end_time,
                ),
            )

            for previous, current in zip(
                ordered,
                ordered[1:],
            ):
                if (
                    current.start_time
                    < previous.end_time
                ):
                    raise (
                        SpecialistCalendarValidationError(
                            "Calendar work intervals "
                            "must not overlap."
                        )
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
            self.repository.get_owned_calendar(
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
            raise (
                SpecialistCalendarNotFoundError(
                    "Professional cabinet "
                    "calendar was not found."
                )
            )

        try:
            rows = await (
                self.repository.replace_schedule(
                    tenant_id=actor.tenant_id,
                    calendar_id=calendar.id,
                    timezone=(
                        normalized_timezone
                    ),
                    slot_duration_minutes=(
                        slot_duration_minutes
                    ),
                    work_intervals=(
                        work_intervals
                    ),
                )
            )

            await (
                self.event_repository
                .create_event(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    event_type=(
                        "change_submitted"
                    ),
                    entity_type=(
                        "professional_cabinet_calendar"
                    ),
                    entity_id=calendar.id,
                    payload={
                        "field": (
                            "weekly_schedule"
                        ),
                        "timezone": (
                            normalized_timezone
                        ),
                        "slot_duration_minutes": (
                            slot_duration_minutes
                        ),
                        "work_intervals_count": (
                            len(work_intervals)
                        ),
                    },
                    platform=platform,
                )
            )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        result = SpecialistCalendarView(
            professional_cabinet_id=(
                professional_cabinet_id
            ),
            timezone=normalized_timezone,
            slot_duration_minutes=(
                slot_duration_minutes
            ),
            is_active=calendar.is_active,
            work_intervals=tuple(
                SpecialistCalendarWorkIntervalView(
                    id=row.id,
                    weekday=row.weekday,
                    start_time=row.start_time,
                    end_time=row.end_time,
                )
                for row in rows
            ),
        )

        return SpecialistCalendarAction(
            actor=actor,
            result=result,
        )

    async def list_exceptions_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
    ) -> SpecialistCalendarExceptionListAction:
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
            self.repository.get_owned_calendar(
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
            raise (
                SpecialistCalendarNotFoundError(
                    "Professional cabinet "
                    "calendar was not found."
                )
            )

        rows = await (
            self.repository.list_exceptions(
                tenant_id=actor.tenant_id,
                calendar_id=calendar.id,
            )
        )

        return (
            SpecialistCalendarExceptionListAction(
                actor=actor,
                result=tuple(
                    SpecialistCalendarExceptionView(
                        id=row.id,
                        exception_date=(
                            row.exception_date
                        ),
                        exception_type=(
                            row.exception_type
                        ),
                        start_time=(
                            row.start_time
                        ),
                        end_time=row.end_time,
                        reason=row.reason,
                    )
                    for row in rows
                ),
            )
        )

    async def create_exception_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        exception_date: date,
        exception_type: str,
        start_time: time | None,
        end_time: time | None,
        reason: str | None,
        platform: str = "telegram",
    ) -> SpecialistCalendarExceptionAction:
        normalized_type = (
            exception_type.strip().lower()
            if isinstance(
                exception_type,
                str,
            )
            else ""
        )

        if normalized_type not in {
            "available",
            "unavailable",
        }:
            raise (
                SpecialistCalendarValidationError(
                    "Calendar exception type "
                    "is not valid."
                )
            )

        has_start = start_time is not None
        has_end = end_time is not None

        if has_start != has_end:
            raise (
                SpecialistCalendarValidationError(
                    "Calendar exception time "
                    "interval is incomplete."
                )
            )

        if (
            has_start
            and has_end
            and start_time >= end_time
        ):
            raise (
                SpecialistCalendarValidationError(
                    "Calendar exception time "
                    "interval is not valid."
                )
            )

        normalized_reason = (
            reason.strip()
            if isinstance(reason, str)
            and reason.strip()
            else None
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
            self.repository.get_owned_calendar(
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
            raise (
                SpecialistCalendarNotFoundError(
                    "Professional cabinet "
                    "calendar was not found."
                )
            )

        try:
            row = await (
                self.repository.create_exception(
                    tenant_id=actor.tenant_id,
                    calendar_id=calendar.id,
                    exception_date=(
                        exception_date
                    ),
                    exception_type=(
                        normalized_type
                    ),
                    start_time=start_time,
                    end_time=end_time,
                    reason=normalized_reason,
                )
            )

            await (
                self.event_repository
                .create_event(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    event_type=(
                        "change_submitted"
                    ),
                    entity_type=(
                        "professional_cabinet_"
                        "calendar_exception"
                    ),
                    entity_id=row.id,
                    payload={
                        "professional_cabinet_id": (
                            str(
                                professional_cabinet_id
                            )
                        ),
                        "exception_date": (
                            exception_date.isoformat()
                        ),
                        "exception_type": (
                            normalized_type
                        ),
                        "has_time_interval": (
                            has_start and has_end
                        ),
                    },
                    platform=platform,
                )
            )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return SpecialistCalendarExceptionAction(
            actor=actor,
            result=(
                SpecialistCalendarExceptionView(
                    id=row.id,
                    exception_date=(
                        row.exception_date
                    ),
                    exception_type=(
                        row.exception_type
                    ),
                    start_time=row.start_time,
                    end_time=row.end_time,
                    reason=row.reason,
                )
            ),
        )

    async def delete_exception_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        exception_id: UUID,
        platform: str = "telegram",
    ) -> SpecialistCalendarExceptionDeleteAction:
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
            self.repository.get_owned_calendar(
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
            raise (
                SpecialistCalendarNotFoundError(
                    "Professional cabinet "
                    "calendar was not found."
                )
            )

        try:
            deleted = await (
                self.repository.delete_exception(
                    tenant_id=actor.tenant_id,
                    calendar_id=calendar.id,
                    exception_id=exception_id,
                )
            )

            if not deleted:
                raise (
                    SpecialistCalendarExceptionNotFoundError(
                        "Calendar exception "
                        "was not found."
                    )
                )

            await (
                self.event_repository
                .create_event(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    event_type=(
                        "change_submitted"
                    ),
                    entity_type=(
                        "professional_cabinet_"
                        "calendar_exception"
                    ),
                    entity_id=exception_id,
                    payload={
                        "professional_cabinet_id": (
                            str(
                                professional_cabinet_id
                            )
                        ),
                        "operation": "deleted",
                    },
                    platform=platform,
                )
            )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return (
            SpecialistCalendarExceptionDeleteAction(
                actor=actor,
                exception_id=exception_id,
            )
        )

    async def list_slots_for_user(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        language: str,
        professional_cabinet_id: UUID,
        date_from: date,
        date_to: date,
    ) -> SpecialistCalendarSlotListAction:
        if (
            date_to < date_from
            or (date_to - date_from).days > 90
        ):
            raise (
                SpecialistCalendarValidationError(
                    "Calendar slot date range "
                    "is not valid."
                )
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
            self.repository.get_owned_calendar(
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
            raise (
                SpecialistCalendarNotFoundError(
                    "Professional cabinet "
                    "calendar was not found."
                )
            )

        try:
            calendar_timezone = ZoneInfo(
                calendar.timezone
            )
        except (
            ZoneInfoNotFoundError,
            ValueError,
        ):
            raise (
                SpecialistCalendarValidationError(
                    "Calendar timezone "
                    "is not valid."
                )
            ) from None

        work_intervals = await (
            self.repository
            .list_active_work_intervals(
                tenant_id=actor.tenant_id,
                calendar_id=calendar.id,
            )
        )
        exceptions = await (
            self.repository
            .list_exceptions_in_range(
                tenant_id=actor.tenant_id,
                calendar_id=calendar.id,
                date_from=date_from,
                date_to=date_to,
            )
        )

        range_start = datetime.combine(
            date_from,
            time.min,
            tzinfo=calendar_timezone,
        ).astimezone(UTC)
        range_end = datetime.combine(
            date_to + timedelta(days=1),
            time.min,
            tzinfo=calendar_timezone,
        ).astimezone(UTC)

        orders = await (
            self.repository
            .list_confirmed_order_intervals(
                tenant_id=actor.tenant_id,
                professional_cabinet_id=(
                    professional_cabinet_id
                ),
                range_start=range_start,
                range_end=range_end,
            )
        )

        if (
            not calendar.is_active
            or calendar.slot_duration_minutes
            <= 0
        ):
            return (
                SpecialistCalendarSlotListAction(
                    actor=actor,
                    timezone=calendar.timezone,
                    result=(),
                )
            )

        schedule_by_weekday: dict[
            int,
            list[object],
        ] = {}
        for interval in work_intervals:
            schedule_by_weekday.setdefault(
                interval.weekday,
                [],
            ).append(interval)

        exceptions_by_date: dict[
            date,
            list[object],
        ] = {}
        for exception in exceptions:
            exceptions_by_date.setdefault(
                exception.exception_date,
                [],
            ).append(exception)

        available_utc: list[
            tuple[datetime, datetime]
        ] = []

        days_count = (
            date_to - date_from
        ).days + 1

        for offset in range(days_count):
            current_date = (
                date_from
                + timedelta(days=offset)
            )
            next_date = (
                current_date
                + timedelta(days=1)
            )

            local_intervals = [
                (
                    datetime.combine(
                        current_date,
                        interval.start_time,
                        tzinfo=(
                            calendar_timezone
                        ),
                    ),
                    datetime.combine(
                        current_date,
                        interval.end_time,
                        tzinfo=(
                            calendar_timezone
                        ),
                    ),
                )
                for interval in (
                    schedule_by_weekday.get(
                        current_date.isoweekday(),
                        [],
                    )
                )
            ]

            day_exceptions = (
                exceptions_by_date.get(
                    current_date,
                    [],
                )
            )

            for exception in day_exceptions:
                if (
                    exception.exception_type
                    != "available"
                ):
                    continue

                if (
                    exception.start_time is None
                    and exception.end_time is None
                ):
                    local_intervals.append(
                        (
                            datetime.combine(
                                current_date,
                                time.min,
                                tzinfo=(
                                    calendar_timezone
                                ),
                            ),
                            datetime.combine(
                                next_date,
                                time.min,
                                tzinfo=(
                                    calendar_timezone
                                ),
                            ),
                        )
                    )
                elif (
                    exception.start_time
                    is not None
                    and exception.end_time
                    is not None
                ):
                    local_intervals.append(
                        (
                            datetime.combine(
                                current_date,
                                exception.start_time,
                                tzinfo=(
                                    calendar_timezone
                                ),
                            ),
                            datetime.combine(
                                current_date,
                                exception.end_time,
                                tzinfo=(
                                    calendar_timezone
                                ),
                            ),
                        )
                    )

            local_intervals = (
                _merge_calendar_intervals(
                    local_intervals
                )
            )

            for exception in day_exceptions:
                if (
                    exception.exception_type
                    != "unavailable"
                ):
                    continue

                if (
                    exception.start_time is None
                    and exception.end_time is None
                ):
                    local_intervals = []
                    break

                if (
                    exception.start_time
                    is not None
                    and exception.end_time
                    is not None
                ):
                    blocked_start = (
                        datetime.combine(
                            current_date,
                            exception.start_time,
                            tzinfo=(
                                calendar_timezone
                            ),
                        )
                    )
                    blocked_end = (
                        datetime.combine(
                            current_date,
                            exception.end_time,
                            tzinfo=(
                                calendar_timezone
                            ),
                        )
                    )
                    local_intervals = (
                        _subtract_calendar_interval(
                            local_intervals,
                            blocked_start,
                            blocked_end,
                        )
                    )

            available_utc.extend(
                (
                    start_at.astimezone(UTC),
                    end_at.astimezone(UTC),
                )
                for start_at, end_at
                in local_intervals
            )

        free_intervals = (
            _merge_calendar_intervals(
                available_utc
            )
        )

        for order in orders:
            if (
                order.start_at is None
                or order.end_at is None
            ):
                continue

            blocked_start = (
                order.start_at.astimezone(UTC)
            )
            blocked_end = (
                order.end_at.astimezone(UTC)
            )
            free_intervals = (
                _subtract_calendar_interval(
                    free_intervals,
                    blocked_start,
                    blocked_end,
                )
            )

        slot_duration = timedelta(
            minutes=(
                calendar.slot_duration_minutes
            )
        )
        slots: list[
            SpecialistCalendarSlotView
        ] = []

        for start_at, end_at in free_intervals:
            cursor = start_at

            while (
                cursor + slot_duration
                <= end_at
            ):
                slot_end = (
                    cursor + slot_duration
                )
                slots.append(
                    SpecialistCalendarSlotView(
                        start_at=cursor,
                        end_at=slot_end,
                    )
                )
                cursor = slot_end

        return SpecialistCalendarSlotListAction(
            actor=actor,
            timezone=calendar.timezone,
            result=tuple(slots),
        )

