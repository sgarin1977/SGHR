"""CON-O001 step 2: canonical read-only Platform snapshot and existing unit catalog."""
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import text
from services.api_identity import ApiActorContext
from services.construction.construction_onboarding import (
    ConstructionOnboardingService, ConstructionOnboardingStep as Step,
    ConstructionOnboardingOperationError as OperationError,
    ConstructionOnboardingStateConflictError as StateConflict,
    ConstructionOnboardingValidationError, _step, _version,
)
from services.construction.construction_permissions import ConstructionPermissionDeniedError
from services.construction.construction_language_context import (
    ConstructionLanguageContext, ConstructionLanguageAccessError,
    ConstructionLanguageConfigurationError, build_construction_language_context_service,
)
from services.construction.construction_currency_settings import (
    OrganizationCurrencyContext, CurrencySettingsDenied, CurrencySettingsConfigurationError,
    build_construction_currency_settings_service,
)
from services.construction.construction_organization_region import (
    OrganizationRegionState, OrganizationRegionDenied, OrganizationRegionConfigurationError, validate_region,
    build_construction_organization_region_service,
)
from services.construction.construction_units import (
    ConstructionUnit, UnitCode, list_construction_units, require_construction_unit,
)
from services.platform_currency_catalog import require_platform_currency


def same_transaction(session, transaction):
    if (transaction is None or session.get_transaction() is not transaction
            or session.in_transaction() is not True or session.get_nested_transaction() is not None):
        raise OperationError("Localization read transaction changed.")


@asynccontextmanager
async def platform_localization_read_context(*, session_context=None):
    if session_context is None:
        from database.session import get_session
        session_context = get_session
    async with session_context() as session:
        if (session.in_transaction() or session.get_nested_transaction() is not None
                or any(bool(getattr(session, name, ())) for name in ("new", "dirty", "deleted"))
                or session.get_bind().dialect.name != "postgresql"):
            raise OperationError("A dedicated clean Platform PostgreSQL session is required.")
        await session.begin()
        transaction = session.get_transaction()
        try:
            await session.execute(text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            same_transaction(session, transaction)
            yield session
            same_transaction(session, transaction)
        finally:
            if session.in_transaction():
                await session.rollback()


def canonical_readers(session):
    return (
        build_construction_language_context_service(platform_session=session),
        build_construction_currency_settings_service(platform_session=session),
        build_construction_organization_region_service(platform_session=session),
    )


class ConstructionOnboardingLocalizationVerifier:
    def __init__(self, *, session, read_context_factory=None, readers_builder=None):
        if (session is None
                or any(not callable(getattr(session, name, None)) for name in (
                    "get_transaction", "in_transaction", "get_nested_transaction"))
                or read_context_factory is not None and not callable(read_context_factory)
                or readers_builder is not None and not callable(readers_builder)):
            raise ValueError("Localization verification requires trusted server collaborators.")
        self.session = session
        self.read_context_factory = read_context_factory or platform_localization_read_context
        self.readers_builder = readers_builder or canonical_readers

    @staticmethod
    def _units_available():
        units = list_construction_units()
        if (not isinstance(units, tuple) or len(units) != len(UnitCode)
                or any(not isinstance(unit, ConstructionUnit) or not isinstance(unit.code, UnitCode)
                       or unit != require_construction_unit(unit.code) for unit in units)
                or {unit.code for unit in units} != set(UnitCode)):
            raise OperationError("The canonical unit catalog is invalid.")

    @staticmethod
    def _coherent(actor, language, currency, region):
        if (not isinstance(language, ConstructionLanguageContext)
                or not isinstance(currency, OrganizationCurrencyContext)
                or not isinstance(region, OrganizationRegionState)
                or language.user_id != actor.user_id
                or any(value.tenant_id != actor.tenant_id for value in (language, currency, region))):
            raise OperationError("Canonical localization context differs.")
        try:
            versions = tuple(_version(value.row_version) for value in (language, currency, region))
        except ConstructionOnboardingValidationError:
            raise OperationError("Canonical organization version is invalid.") from None
        if len(set(versions)) != 1:
            raise StateConflict("Organization settings changed during verification.")
        stamps = (language.organization_updated_at, currency.updated_at, region.updated_at)
        if any(not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None for stamp in stamps):
            raise OperationError("Canonical organization timestamp is invalid.")
        if len(set(stamps)) != 1:
            raise StateConflict("Organization settings changed during verification.")
        if not region.configured:
            return False
        validate_region(country_code=region.country_code, timezone_code=region.timezone)
        # The canonical readers validate active language membership, active
        # country, IANA timezone and currency. Never substitute caller defaults.
        if not any(item.code == language.organization_default_language
                   and item.enabled_for_organization is True for item in language.languages):
            raise OperationError("Canonical organization language is invalid.")
        if not any(item.code == language.interface_language for item in language.languages):
            raise OperationError("Canonical profile language is invalid.")
        require_platform_currency(currency.default_currency)
        if not any(item.code == currency.default_currency for item in currency.currencies):
            raise OperationError("Canonical currency catalog is invalid.")
        return True

    async def verify_step(self, *, actor, step):
        ConstructionOnboardingService._actor(actor)
        if not isinstance(actor, ApiActorContext) or _step(step) is not Step.LOCALIZATION:
            raise OperationError("A canonical Platform actor and localization step are required.")
        transaction = self.session.get_transaction()
        same_transaction(self.session, transaction)
        try:
            async with self.read_context_factory() as platform_session:
                if platform_session is self.session:
                    raise OperationError("Platform reads must not reuse the progress session.")
                platform_transaction = platform_session.get_transaction()
                same_transaction(platform_session, platform_transaction)
                readers = self.readers_builder(platform_session)
                if (not isinstance(readers, tuple) or len(readers) != 3
                        or any(getattr(reader, "session", None) is not platform_session
                               or not callable(getattr(reader, "read_for_actor", None))
                               for reader in readers)):
                    raise OperationError("Canonical localization readers are invalid.")
                results = []
                for reader in readers:
                    results.append(await reader.read_for_actor(actor=actor))
                    same_transaction(platform_session, platform_transaction)
                    same_transaction(self.session, transaction)
                self._units_available()
                ready = self._coherent(actor, *results)
            same_transaction(self.session, transaction)
            return ready
        except (ConstructionLanguageAccessError, CurrencySettingsDenied, OrganizationRegionDenied):
            raise ConstructionPermissionDeniedError("Localization authority is unavailable.") from None
        except (ConstructionLanguageConfigurationError, CurrencySettingsConfigurationError,
                OrganizationRegionConfigurationError):
            same_transaction(self.session, transaction)
            return False
        except (OperationError, StateConflict, ConstructionPermissionDeniedError):
            raise
        except Exception:
            raise OperationError("Canonical localization verification failed.") from None
