from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)

from api.auth import (
    require_permission,
    require_specialist_actor,
)
from api.dependencies import (
    get_specialist_calendar_service,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    SpecialistCalendarExceptionCreateRequest,
    SpecialistCalendarExceptionDeleteResponse,
    SpecialistCalendarExceptionListResponse,
    SpecialistCalendarExceptionResponse,
    SpecialistCalendarResponse,
    SpecialistCalendarScheduleUpdateRequest,
    SpecialistCalendarSlotListResponse,
)
from services.api_identity import (
    ApiActorContext,
)
from services.specialist import (
    SpecialistRegistrationError,
)
from services.specialist_calendar import (
    SpecialistCalendarExceptionNotFoundError,
    SpecialistCalendarNotFoundError,
    SpecialistCalendarScheduleInterval,
    SpecialistCalendarService,
    SpecialistCalendarValidationError,
)
from services.specialist_cabinets import (
    SpecialistCabinetsAccessError,
    SpecialistCabinetsSelectionError,
)


router = APIRouter()

SPECIALIST_CALENDAR_READ_PERMISSION = (
    "specialist.calendar.read"
)
require_specialist_calendar_read = (
    require_permission(
        SPECIALIST_CALENDAR_READ_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


SPECIALIST_CALENDAR_WRITE_PERMISSION = (
    "specialist.calendar.write"
)
require_specialist_calendar_write = (
    require_permission(
        SPECIALIST_CALENDAR_WRITE_PERMISSION,
        actor_dependency=(
            require_specialist_actor
        ),
    )
)


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/calendar"
    ),
    response_model=SpecialistCalendarResponse,
)
async def get_specialist_calendar(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_calendar_read
        ),
    ],
    service: Annotated[
        SpecialistCalendarService,
        Depends(
            get_specialist_calendar_service
        ),
    ],
):
    try:
        action = await (
            service.get_calendar_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
            )
        )
    except (
        SpecialistCalendarNotFoundError,
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="calendar_not_found",
            message=(
                "Professional cabinet "
                "calendar was not found."
            ),
        ) from None

    result = action.result

    return success_envelope(
        data={
            "professional_cabinet_id": (
                result.professional_cabinet_id
            ),
            "timezone": result.timezone,
            "slot_duration_minutes": (
                result.slot_duration_minutes
            ),
            "is_active": result.is_active,
            "work_intervals": [
                {
                    "id": interval.id,
                    "weekday": (
                        interval.weekday
                    ),
                    "start_time": (
                        interval.start_time
                    ),
                    "end_time": (
                        interval.end_time
                    ),
                }
                for interval
                in result.work_intervals
            ],
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.put(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/calendar/schedule"
    ),
    response_model=SpecialistCalendarResponse,
)
async def update_specialist_calendar_schedule(
    cabinet_id: UUID,
    payload: (
        SpecialistCalendarScheduleUpdateRequest
    ),
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_calendar_write
        ),
    ],
    service: Annotated[
        SpecialistCalendarService,
        Depends(
            get_specialist_calendar_service
        ),
    ],
):
    try:
        action = await (
            service.update_schedule_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                timezone=payload.timezone,
                slot_duration_minutes=(
                    payload.slot_duration_minutes
                ),
                work_intervals=tuple(
                    SpecialistCalendarScheduleInterval(
                        weekday=(
                            interval.weekday
                        ),
                        start_time=(
                            interval.start_time
                        ),
                        end_time=(
                            interval.end_time
                        ),
                    )
                    for interval
                    in payload.work_intervals
                ),
                platform="api",
            )
        )
    except SpecialistCalendarValidationError:
        raise ApiHttpError(
            status_code=422,
            code="calendar_validation_error",
            message=(
                "Calendar schedule "
                "is not valid."
            ),
        ) from None
    except (
        SpecialistCalendarNotFoundError,
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="calendar_not_found",
            message=(
                "Professional cabinet "
                "calendar was not found."
            ),
        ) from None

    result = action.result

    return success_envelope(
        data={
            "professional_cabinet_id": (
                result.professional_cabinet_id
            ),
            "timezone": result.timezone,
            "slot_duration_minutes": (
                result.slot_duration_minutes
            ),
            "is_active": result.is_active,
            "work_intervals": [
                {
                    "id": interval.id,
                    "weekday": (
                        interval.weekday
                    ),
                    "start_time": (
                        interval.start_time
                    ),
                    "end_time": (
                        interval.end_time
                    ),
                }
                for interval
                in result.work_intervals
            ],
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/calendar/exceptions"
    ),
    response_model=(
        SpecialistCalendarExceptionListResponse
    ),
)
async def list_specialist_calendar_exceptions(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_calendar_read
        ),
    ],
    service: Annotated[
        SpecialistCalendarService,
        Depends(
            get_specialist_calendar_service
        ),
    ],
):
    try:
        action = await (
            service.list_exceptions_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
            )
        )
    except (
        SpecialistCalendarNotFoundError,
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="calendar_not_found",
            message=(
                "Professional cabinet "
                "calendar was not found."
            ),
        ) from None

    return success_envelope(
        data=[
            {
                "id": item.id,
                "exception_date": (
                    item.exception_date
                ),
                "exception_type": (
                    item.exception_type
                ),
                "start_time": (
                    item.start_time
                ),
                "end_time": item.end_time,
                "reason": item.reason,
            }
            for item in action.result
        ],
        meta={},
        request_id=request.state.request_id,
    )


@router.post(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/calendar/exceptions"
    ),
    response_model=(
        SpecialistCalendarExceptionResponse
    ),
    status_code=201,
)
async def create_specialist_calendar_exception(
    cabinet_id: UUID,
    payload: (
        SpecialistCalendarExceptionCreateRequest
    ),
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_calendar_write
        ),
    ],
    service: Annotated[
        SpecialistCalendarService,
        Depends(
            get_specialist_calendar_service
        ),
    ],
):
    try:
        action = await (
            service.create_exception_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                exception_date=(
                    payload.exception_date
                ),
                exception_type=(
                    payload.exception_type
                ),
                start_time=payload.start_time,
                end_time=payload.end_time,
                reason=payload.reason,
                platform="api",
            )
        )
    except SpecialistCalendarValidationError:
        raise ApiHttpError(
            status_code=422,
            code="calendar_validation_error",
            message=(
                "Calendar exception "
                "is not valid."
            ),
        ) from None
    except (
        SpecialistCalendarNotFoundError,
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="calendar_not_found",
            message=(
                "Professional cabinet "
                "calendar was not found."
            ),
        ) from None

    result = action.result

    return success_envelope(
        data={
            "id": result.id,
            "exception_date": (
                result.exception_date
            ),
            "exception_type": (
                result.exception_type
            ),
            "start_time": result.start_time,
            "end_time": result.end_time,
            "reason": result.reason,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.delete(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/calendar/exceptions/"
        "{exception_id}"
    ),
    response_model=(
        SpecialistCalendarExceptionDeleteResponse
    ),
)
async def delete_specialist_calendar_exception(
    cabinet_id: UUID,
    exception_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_calendar_write
        ),
    ],
    service: Annotated[
        SpecialistCalendarService,
        Depends(
            get_specialist_calendar_service
        ),
    ],
):
    try:
        action = await (
            service.delete_exception_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                exception_id=exception_id,
                platform="api",
            )
        )
    except (
        SpecialistCalendarExceptionNotFoundError
    ):
        raise ApiHttpError(
            status_code=404,
            code="calendar_exception_not_found",
            message=(
                "Calendar exception "
                "was not found."
            ),
        ) from None
    except (
        SpecialistCalendarNotFoundError,
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="calendar_not_found",
            message=(
                "Professional cabinet "
                "calendar was not found."
            ),
        ) from None

    return success_envelope(
        data={
            "id": action.exception_id,
            "deleted": True,
        },
        meta={},
        request_id=request.state.request_id,
    )


@router.get(
    (
        "/specialist/cabinets/"
        "{cabinet_id}/calendar/slots"
    ),
    response_model=(
        SpecialistCalendarSlotListResponse
    ),
)
async def list_specialist_calendar_slots(
    cabinet_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(
            require_specialist_calendar_read
        ),
    ],
    service: Annotated[
        SpecialistCalendarService,
        Depends(
            get_specialist_calendar_service
        ),
    ],
    date_from: Annotated[
        date,
        Query(alias="from"),
    ],
    date_to: Annotated[
        date,
        Query(alias="to"),
    ],
):
    try:
        action = await (
            service.list_slots_for_user(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                language=actor.language_code,
                professional_cabinet_id=(
                    cabinet_id
                ),
                date_from=date_from,
                date_to=date_to,
            )
        )
    except SpecialistCalendarValidationError:
        raise ApiHttpError(
            status_code=422,
            code="calendar_validation_error",
            message=(
                "Calendar slot range "
                "is not valid."
            ),
        ) from None
    except (
        SpecialistCalendarNotFoundError,
        SpecialistCabinetsAccessError,
        SpecialistCabinetsSelectionError,
        SpecialistRegistrationError,
    ):
        raise ApiHttpError(
            status_code=404,
            code="calendar_not_found",
            message=(
                "Professional cabinet "
                "calendar was not found."
            ),
        ) from None

    return success_envelope(
        data={
            "timezone": action.timezone,
            "items": [
                {
                    "start_at": slot.start_at,
                    "end_at": slot.end_at,
                }
                for slot in action.result
            ],
        },
        meta={},
        request_id=request.state.request_id,
    )

