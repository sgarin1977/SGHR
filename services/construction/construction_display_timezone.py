"""Narrow Platform read for authorized Construction presentation."""
from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_organization_region import (
    ConstructionOrganizationRegionService,
)
from utils.construction_display_timezone import (
    validate_timezone, validate_display_timezone,
)


class ConstructionDisplayTimezoneReader:
    def __init__(self, *, session, identity, repository):
        if (
            session is None
            or getattr(identity, "session", None) is not session
            or getattr(repository, "session", None) is not session
        ):
            raise ValueError("One Platform session is required.")
        self.session = session
        self.identity = identity
        self.repository = repository

    async def _current(self, actor):
        if not isinstance(actor, ApiActorContext) or actor.status != "active":
            raise ApiIdentityAccessError("Platform identity is unavailable.")
        self.session.expire_all()
        current = await self.identity.require_actor(
            user_id=actor.user_id, tenant_id=actor.tenant_id,
        )
        if (
            not isinstance(current, ApiActorContext)
            or current.status != "active"
            or current.user_id != actor.user_id
            or current.tenant_id != actor.tenant_id
        ):
            raise ApiIdentityAccessError("Platform identity context changed.")
        return current

    async def read_for_actor(self, *, actor):
        if any(
            bool(getattr(self.session, name, ()))
            for name in ("new", "dirty", "deleted")
        ):
            raise RuntimeError("A clean Platform read session is required.")
        with self.session.no_autoflush:
            await self._current(actor)
            row = await self.repository.get_active(tenant_id=actor.tenant_id)
            region = ConstructionOrganizationRegionService._state(
                row, actor.tenant_id,
            )
            current = await self._current(actor)
            user_zone = validate_timezone(current.timezone)
            organization_zone = validate_timezone(region.timezone)
            if user_zone is not None:
                result = {"timezone": user_zone, "source": "USER"}
            elif organization_zone is not None:
                result = {
                    "timezone": organization_zone, "source": "ORGANIZATION",
                }
            else:
                result = {"timezone": None, "source": "UNCONFIGURED"}
            return validate_display_timezone(result)


def build_display_timezone_reader(session):
    from services.api_identity import ApiIdentityService
    from database.repositories.platform_organization_region import (
        PlatformOrganizationRegionRepository,
    )
    return ConstructionDisplayTimezoneReader(
        session=session,
        identity=ApiIdentityService(session),
        repository=PlatformOrganizationRegionRepository(session),
    )
