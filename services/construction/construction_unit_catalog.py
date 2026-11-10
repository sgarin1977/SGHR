"""Read the system unit catalog with fresh Platform membership."""
from dataclasses import dataclass
from uuid import UUID

from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_units import ConstructionUnit, list_construction_units
from services.construction.construction_unit_labels import unit_label_source
from services.platform_catalog_localization import (
    CatalogLabel, CatalogLanguageUnavailable, CatalogTranslationUnavailable,
    build_platform_catalog_localizer,
)


class ConstructionUnitCatalogDenied(Exception):
    pass


class ConstructionUnitCatalogOperationError(Exception):
    pass


class ConstructionUnitCatalogLanguageError(ConstructionUnitCatalogOperationError):
    code = "LANGUAGE_CONFIGURATION_REQUIRED"


class ConstructionUnitCatalogLanguageChanged(ConstructionUnitCatalogLanguageError):
    code = "LANGUAGE_CONTEXT_CHANGED"


class ConstructionUnitCatalogTranslationError(ConstructionUnitCatalogOperationError):
    pass


@dataclass(frozen=True)
class ConstructionUnitCatalogContext:
    tenant_id: UUID
    units: tuple[ConstructionUnit, ...]
    language_code: str
    source_version: int
    labels: tuple[CatalogLabel, ...]


class ConstructionUnitCatalogService:
    def __init__(self, *, session, identity, localizer=None):
        if (
            session is None or not hasattr(session, "no_autoflush")
            or getattr(identity, "session", None) is not session
        ):
            raise ValueError("Unit catalog requires shared Platform identity reads.")
        self.session, self.identity = session, identity
        self.localizer = localizer if localizer is not None else build_platform_catalog_localizer(session)
        if getattr(self.localizer, "session", None) is not session:
            raise ValueError("Unit localization must share the Platform read session.")

    async def _current(self, actor):
        if (
            not isinstance(actor, ApiActorContext) or actor.status != "active"
            or not isinstance(actor.user_id, UUID)
            or not isinstance(actor.tenant_id, UUID)
        ):
            raise ConstructionUnitCatalogDenied("Unit catalog is unavailable.")
        self.session.expire_all()
        current = await self.identity.require_actor(
            user_id=actor.user_id, tenant_id=actor.tenant_id,
        )
        if (
            not isinstance(current, ApiActorContext) or current.status != "active"
            or current.user_id != actor.user_id or current.tenant_id != actor.tenant_id
        ):
            raise ConstructionUnitCatalogDenied("Unit catalog is unavailable.")
        return current

    async def read_for_actor(self, *, actor):
        owns_cache_write = False
        try:
            if any(bool(getattr(self.session, name)) for name in ("new", "dirty", "deleted")):
                raise ConstructionUnitCatalogOperationError("A clean Platform read session is required.")
            with self.session.no_autoflush:
                current = await self._current(actor)
                units = list_construction_units()
                version, source, supplied = unit_label_source()
                prepared = await self.localizer.prepare(
                    namespace="construction.units", source_version=version,
                    source_labels=source,
                    supplied_labels=supplied.get(current.language_code, {}),
                    language_code=current.language_code,
                )
                current = await self._current(actor)
                if current.language_code != prepared.language_code:
                    raise ConstructionUnitCatalogLanguageChanged("Profile language changed.")
                result = ConstructionUnitCatalogContext(
                    current.tenant_id, units, prepared.language_code, version, prepared.labels,
                )
                if prepared.pending:
                    owns_cache_write = True
                    await self.localizer.persist(prepared)
                    current = await self._current(actor)
                    if current.language_code != prepared.language_code:
                        raise ConstructionUnitCatalogLanguageChanged("Profile language changed.")
                    await self.session.commit()
                return result
        except BaseException as error:
            if owns_cache_write:
                await self.session.rollback()
            if not isinstance(error, Exception):
                raise
            if isinstance(error, ApiIdentityAccessError):
                raise ConstructionUnitCatalogDenied("Unit catalog is unavailable.") from None
            if isinstance(error, CatalogLanguageUnavailable):
                raise ConstructionUnitCatalogLanguageError("Profile language is unavailable.") from None
            if isinstance(error, CatalogTranslationUnavailable):
                raise ConstructionUnitCatalogTranslationError("Unit translation is unavailable.") from None
            if isinstance(error, (ConstructionUnitCatalogDenied, ConstructionUnitCatalogOperationError)):
                raise
            raise ConstructionUnitCatalogOperationError("Unit catalog read failed.") from None


def build_construction_unit_catalog_service(*, platform_session):
    from services.api_identity import ApiIdentityService
    return ConstructionUnitCatalogService(
        session=platform_session, identity=ApiIdentityService(platform_session),
    )
