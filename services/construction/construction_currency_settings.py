"""CON-012 organization currency; shared Platform identity, authority and storage."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.platform_currency_catalog import (
    PlatformCurrencyCodeError,
    PlatformCurrencyDefinition,
    list_platform_currencies,
    require_platform_currency,
)
from services.construction.construction_organization_language import (
    ConstructionOrganizationLanguageAuthorizer as OrganizationSettingsAuthorizer,
    OrganizationLanguageDenied as OrganizationSettingsDenied,
)


class CurrencySettingsDenied(Exception):
    pass


class CurrencySettingsValidationError(ValueError):
    pass


class CurrencySettingsConfigurationError(Exception):
    pass


class CurrencySettingsConflict(Exception):
    pass


class CurrencySettingsOperationError(Exception):
    pass


@dataclass(frozen=True)
class OrganizationCurrencyState:
    tenant_id: UUID
    default_currency: str
    updated_at: datetime
    row_version: int


@dataclass(frozen=True)
class OrganizationCurrencyContext(OrganizationCurrencyState):
    currencies: tuple[PlatformCurrencyDefinition, ...]


@dataclass(frozen=True)
class OrganizationCurrencyChange(OrganizationCurrencyState):
    changed: bool


def _utc(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CurrencySettingsConfigurationError(
            "Organization timestamp is unavailable."
        )
    return value.astimezone(timezone.utc)


class ConstructionCurrencySettingsService:
    def __init__(
        self, *, session, repository, identity,
        authorizer, events, now_provider=None,
    ):
        if (
            session is None
            or any(
                getattr(value, "session", None) is not session
                for value in (repository, identity, authorizer, events)
            )
        ):
            raise ValueError(
                "Currency settings and audit require one Platform session."
            )

        self.session = session
        self.repository = repository
        self.identity = identity
        self.authorizer = authorizer
        self.events = events
        self.now_provider = now_provider or (
            lambda: datetime.now(timezone.utc)
        )

    def _clean(self):
        if any(
            bool(getattr(self.session, name))
            for name in ("new", "dirty", "deleted")
        ):
            raise CurrencySettingsOperationError(
                "A clean Platform session is required."
            )

    @staticmethod
    def _state(row, *, tenant_id):
        if row is None or row.get("tenant_id") != tenant_id:
            raise CurrencySettingsDenied(
                "Organization settings are unavailable."
            )

        version = row.get("row_version")
        code = row.get("default_currency")
        if (
            type(version) is not int
            or not 1 <= version <= 2**63 - 1
            or not isinstance(code, str)
            or len(code) > 3
        ):
            raise CurrencySettingsConfigurationError(
                "Organization currency configuration is invalid."
            )

        return OrganizationCurrencyState(
            tenant_id, code, _utc(row.get("updated_at")), version,
        )

    async def _fresh(self, actor):
        if (
            not isinstance(actor, ApiActorContext)
            or actor.status != "active"
            or not isinstance(actor.user_id, UUID)
            or not isinstance(actor.tenant_id, UUID)
        ):
            raise CurrencySettingsDenied(
                "Organization settings are unavailable."
            )

        self.session.expire_all()
        current = await self.identity.require_actor(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        if (
            not isinstance(current, ApiActorContext)
            or current.status != "active"
            or current.user_id != actor.user_id
            or current.tenant_id != actor.tenant_id
        ):
            raise CurrencySettingsDenied(
                "Organization settings are unavailable."
            )
        return current

    async def read_for_actor(self, *, actor):
        self._clean()
        try:
            with self.session.no_autoflush:
                current = await self._fresh(actor)
                row = await self.repository.get_active(
                    tenant_id=current.tenant_id
                )
                state = self._state(row, tenant_id=current.tenant_id)
                try:
                    require_platform_currency(state.default_currency)
                except PlatformCurrencyCodeError:
                    raise CurrencySettingsConfigurationError(
                        "Organization currency must be configured."
                    ) from None

                currencies = list_platform_currencies()
                current = await self._fresh(actor)
                return OrganizationCurrencyContext(
                    current.tenant_id,
                    state.default_currency,
                    state.updated_at,
                    state.row_version,
                    currencies,
                )
        except ApiIdentityAccessError:
            raise CurrencySettingsDenied(
                "Organization settings are unavailable."
            ) from None
        except (CurrencySettingsDenied, CurrencySettingsConfigurationError):
            raise
        except Exception:
            raise CurrencySettingsOperationError(
                "Currency settings read failed."
            ) from None

    async def update_for_actor(
        self, *, actor, currency_code, expected_row_version, trace_id=None,
    ):
        self._clean()
        try:
            try:
                target = require_platform_currency(currency_code)
            except PlatformCurrencyCodeError:
                raise CurrencySettingsValidationError(
                    "Currency code is invalid."
                ) from None

            if (
                type(expected_row_version) is not int
                or not 1 <= expected_row_version <= 2**63 - 1
            ):
                raise CurrencySettingsValidationError(
                    "Expected row version is invalid."
                )
            if trace_id is not None and (
                not isinstance(trace_id, str)
                or not trace_id.strip()
                or len(trace_id) > 255
            ):
                raise CurrencySettingsValidationError("Audit trace is invalid.")

            current = await self.authorizer.require(actor=actor)
            row = await self.repository.get_active(
                tenant_id=current.tenant_id,
                for_update=True,
            )
            current = await self.authorizer.require(actor=actor)
            previous = self._state(row, tenant_id=current.tenant_id)

            if previous.row_version != expected_row_version:
                raise CurrencySettingsConflict(
                    "Organization settings changed."
                )

            if previous.default_currency == target.code:
                result = OrganizationCurrencyChange(
                    previous.tenant_id,
                    previous.default_currency,
                    previous.updated_at,
                    previous.row_version,
                    False,
                )
                await self.session.commit()
                return result

            if previous.row_version == 2**63 - 1:
                raise CurrencySettingsOperationError(
                    "Organization row version is exhausted."
                )

            saved = await self.repository.replace_default(
                tenant_id=current.tenant_id,
                previous_currency=previous.default_currency,
                expected_row_version=previous.row_version,
                currency_code=target.code,
                as_of=_utc(self.now_provider()),
            )
            if saved is None:
                raise CurrencySettingsConflict(
                    "Organization settings changed."
                )
            if saved.get("tenant_id") != current.tenant_id:
                raise CurrencySettingsOperationError(
                    "Currency update result is invalid."
                )

            result_state = self._state(saved, tenant_id=current.tenant_id)
            if (
                result_state.default_currency != target.code
                or result_state.row_version != previous.row_version + 1
            ):
                raise CurrencySettingsOperationError(
                    "Currency update result is invalid."
                )

            await self.events.create_event(
                event_type="construction_organization_currency_updated",
                tenant_id=current.tenant_id,
                user_id=current.user_id,
                entity_type="tenant",
                entity_id=current.tenant_id,
                payload={
                    "field": "default_currency",
                    "old_value": previous.default_currency,
                    "new_value": target.code,
                    "previous_row_version": previous.row_version,
                    "row_version": result_state.row_version,
                },
                platform="api",
                trace_id=trace_id,
            )
            result = OrganizationCurrencyChange(
                result_state.tenant_id,
                result_state.default_currency,
                result_state.updated_at,
                result_state.row_version,
                True,
            )
            await self.session.commit()
            return result
        except BaseException as exc:
            await self.session.rollback()
            if isinstance(exc, OrganizationSettingsDenied):
                raise CurrencySettingsDenied(
                    "Organization currency management is unavailable."
                ) from None
            if isinstance(exc, (
                CurrencySettingsDenied,
                CurrencySettingsValidationError,
                CurrencySettingsConfigurationError,
                CurrencySettingsConflict,
                CurrencySettingsOperationError,
            )):
                raise
            if isinstance(exc, Exception):
                raise CurrencySettingsOperationError(
                    "Currency settings update failed."
                ) from None
            raise


def build_construction_currency_settings_service(*, platform_session):
    from database.repositories.event import EventRepository
    from database.repositories.platform_organization_currency import (
        PlatformOrganizationCurrencyRepository,
    )
    from services.api_identity import ApiIdentityService
    from services.construction.construction_grants import (
        build_construction_access_context_resolver,
    )

    identity = ApiIdentityService(platform_session)
    return ConstructionCurrencySettingsService(
        session=platform_session,
        repository=PlatformOrganizationCurrencyRepository(platform_session),
        identity=identity,
        authorizer=OrganizationSettingsAuthorizer(
            session=platform_session,
            identity=identity,
            access_resolver=build_construction_access_context_resolver(
                platform_session
            ),
        ),
        events=EventRepository(platform_session),
    )

# con_012_currency_settings_v1
