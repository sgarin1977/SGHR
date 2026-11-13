"""Optional presentation context on the existing navigation operation."""
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, Query

from api.dependencies import get_api_session
from api.errors import ApiHttpError
from services.api_identity import ApiIdentityAccessError
from services.construction.construction_display_timezone import (
    build_display_timezone_reader,
)
from services.construction.construction_organization_region import (
    OrganizationRegionDenied, OrganizationRegionConfigurationError,
)
from utils.construction_display_timezone import validate_display_timezone


async def get_navigation_timezone_session(
    include_timezone: Literal["false", "true"] = Query(default="false"),
):
    if include_timezone == "false":
        yield None
        return
    async with asynccontextmanager(get_api_session)() as session:
        yield session


def get_optional_navigation_timezone(
    session=Depends(get_navigation_timezone_session),
):
    return None if session is None else build_display_timezone_reader(session)


async def read_navigation_timezone(reader, *, actor):
    try:
        return validate_display_timezone(
            await reader.read_for_actor(actor=actor),
        )
    except (ApiIdentityAccessError, OrganizationRegionDenied):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Organization presentation is unavailable.",
        ) from None
    except (ValueError, OrganizationRegionConfigurationError):
        raise ApiHttpError(
            status_code=409, code="TIMEZONE_CONFIGURATION_REQUIRED",
            message="A valid display timezone configuration is required.",
        ) from None
    except Exception:
        raise ApiHttpError(
            status_code=500, code="internal_error",
            message="Organization presentation failed.",
        ) from None
