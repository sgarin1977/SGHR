from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Protocol
from uuid import UUID

from sqlalchemy import (
    and_,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from database.models import (
    ProfessionalCabinet,
    ProfessionalCabinetCalendar,
    ProfessionalCabinetCalendarException,
    ProfessionalCabinetWorkInterval,
    ServiceOrder,
)



class CalendarScheduleIntervalData(
    Protocol
):
    weekday: int
    start_time: time
    end_time: time


class SpecialistCalendarRepository:
    def __init__(
        self,
        session: AsyncSession,
    ):
        self.session = session

    async def get_owned_calendar(
        self,
        *,
        tenant_id: UUID,
        specialist_id: UUID,
        professional_cabinet_id: UUID,
    ) -> ProfessionalCabinetCalendar | None:
        result = await self.session.execute(
            select(
                ProfessionalCabinetCalendar
            )
            .join(
                ProfessionalCabinet,
                and_(
                    ProfessionalCabinet.tenant_id
                    == ProfessionalCabinetCalendar.tenant_id,
                    ProfessionalCabinet.id
                    == ProfessionalCabinetCalendar.professional_cabinet_id,
                ),
            )
            .where(
                ProfessionalCabinetCalendar.tenant_id
                == tenant_id,
                ProfessionalCabinetCalendar.professional_cabinet_id
                == professional_cabinet_id,
                ProfessionalCabinet.tenant_id
                == tenant_id,
                ProfessionalCabinet.specialist_id
                == specialist_id,
                ProfessionalCabinet.id
                == professional_cabinet_id,
            )
            .limit(1)
        )

        return result.scalar_one_or_none()

    async def list_active_work_intervals(
        self,
        *,
        tenant_id: UUID,
        calendar_id: UUID,
    ) -> list[
        ProfessionalCabinetWorkInterval
    ]:
        result = await self.session.execute(
            select(
                ProfessionalCabinetWorkInterval
            )
            .where(
                ProfessionalCabinetWorkInterval.tenant_id
                == tenant_id,
                ProfessionalCabinetWorkInterval.calendar_id
                == calendar_id,
                ProfessionalCabinetWorkInterval.is_active.is_(
                    True
                ),
            )
            .order_by(
                ProfessionalCabinetWorkInterval.weekday.asc(),
                ProfessionalCabinetWorkInterval.start_time.asc(),
                ProfessionalCabinetWorkInterval.id.asc(),
            )
        )

        return list(result.scalars().all())

    async def replace_schedule(
        self,
        *,
        tenant_id: UUID,
        calendar_id: UUID,
        timezone: str,
        slot_duration_minutes: int,
        work_intervals: Sequence[
            CalendarScheduleIntervalData
        ],
    ) -> list[
        ProfessionalCabinetWorkInterval
    ]:
        await self.session.execute(
            update(
                ProfessionalCabinetCalendar
            )
            .where(
                ProfessionalCabinetCalendar.tenant_id
                == tenant_id,
                ProfessionalCabinetCalendar.id
                == calendar_id,
            )
            .values(
                timezone=timezone,
                slot_duration_minutes=(
                    slot_duration_minutes
                ),
                updated_at=func.now(),
            )
        )

        await self.session.execute(
            delete(
                ProfessionalCabinetWorkInterval
            )
            .where(
                ProfessionalCabinetWorkInterval.tenant_id
                == tenant_id,
                ProfessionalCabinetWorkInterval.calendar_id
                == calendar_id,
            )
        )

        rows = [
            ProfessionalCabinetWorkInterval(
                tenant_id=tenant_id,
                calendar_id=calendar_id,
                weekday=interval.weekday,
                start_time=interval.start_time,
                end_time=interval.end_time,
                is_active=True,
            )
            for interval in work_intervals
        ]

        self.session.add_all(rows)
        await self.session.flush()

        return rows

    async def list_exceptions(
        self,
        *,
        tenant_id: UUID,
        calendar_id: UUID,
    ) -> list[
        ProfessionalCabinetCalendarException
    ]:
        result = await self.session.execute(
            select(
                ProfessionalCabinetCalendarException
            )
            .where(
                ProfessionalCabinetCalendarException.tenant_id
                == tenant_id,
                ProfessionalCabinetCalendarException.calendar_id
                == calendar_id,
            )
            .order_by(
                ProfessionalCabinetCalendarException.exception_date.asc(),
                ProfessionalCabinetCalendarException.start_time.asc(),
                ProfessionalCabinetCalendarException.id.asc(),
            )
        )

        return list(result.scalars().all())

    async def create_exception(
        self,
        *,
        tenant_id: UUID,
        calendar_id: UUID,
        exception_date: date,
        exception_type: str,
        start_time: time | None,
        end_time: time | None,
        reason: str | None,
    ) -> ProfessionalCabinetCalendarException:
        row = (
            ProfessionalCabinetCalendarException(
                tenant_id=tenant_id,
                calendar_id=calendar_id,
                exception_date=exception_date,
                exception_type=exception_type,
                start_time=start_time,
                end_time=end_time,
                reason=reason,
            )
        )

        self.session.add(row)
        await self.session.flush()

        return row

    async def delete_exception(
        self,
        *,
        tenant_id: UUID,
        calendar_id: UUID,
        exception_id: UUID,
    ) -> bool:
        result = await self.session.execute(
            delete(
                ProfessionalCabinetCalendarException
            )
            .where(
                ProfessionalCabinetCalendarException.tenant_id
                == tenant_id,
                ProfessionalCabinetCalendarException.calendar_id
                == calendar_id,
                ProfessionalCabinetCalendarException.id
                == exception_id,
            )
            .returning(
                ProfessionalCabinetCalendarException.id
            )
        )

        return (
            result.scalar_one_or_none()
            is not None
        )

    async def list_confirmed_order_intervals(
        self,
        *,
        tenant_id: UUID,
        professional_cabinet_id: UUID,
        range_start: datetime,
        range_end: datetime,
    ) -> list[ServiceOrder]:
        result = await self.session.execute(
            select(ServiceOrder)
            .where(
                ServiceOrder.tenant_id
                == tenant_id,
                ServiceOrder.professional_cabinet_id
                == professional_cabinet_id,
                ServiceOrder.status
                == "confirmed",
                ServiceOrder.start_at.is_not(
                    None
                ),
                ServiceOrder.end_at.is_not(
                    None
                ),
                ServiceOrder.start_at
                < range_end,
                ServiceOrder.end_at
                > range_start,
            )
            .order_by(
                ServiceOrder.start_at.asc(),
                ServiceOrder.end_at.asc(),
                ServiceOrder.id.asc(),
            )
        )

        return list(result.scalars().all())

    async def list_exceptions_in_range(
        self,
        *,
        tenant_id: UUID,
        calendar_id: UUID,
        date_from: date,
        date_to: date,
    ) -> list[
        ProfessionalCabinetCalendarException
    ]:
        result = await self.session.execute(
            select(
                ProfessionalCabinetCalendarException
            )
            .where(
                ProfessionalCabinetCalendarException.tenant_id
                == tenant_id,
                ProfessionalCabinetCalendarException.calendar_id
                == calendar_id,
                ProfessionalCabinetCalendarException.exception_date
                >= date_from,
                ProfessionalCabinetCalendarException.exception_date
                <= date_to,
            )
            .order_by(
                ProfessionalCabinetCalendarException.exception_date.asc(),
                ProfessionalCabinetCalendarException.start_time.asc(),
                ProfessionalCabinetCalendarException.id.asc(),
            )
        )

        return list(result.scalars().all())

