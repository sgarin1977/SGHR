"""CON-011 organization language, canonical Platform transaction; no public route."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from services.api_identity import ApiActorContext, ApiIdentityAccessError
from services.construction.construction_tenant_settings import (
    ConstructionTenantSettingsPermissionDeniedError,
    require_construction_settings_manage,
)
from services.platform_company_profile_access import (
    CompanyProfilePermissionDeniedError,
    require_company_profile_manage,
)


class OrganizationLanguageDenied(Exception):
    pass


class OrganizationLanguageValidationError(ValueError):
    pass


class OrganizationLanguageConflict(Exception):
    pass


class OrganizationLanguageOperationError(Exception):
    pass


def _utc(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise OrganizationLanguageValidationError(
            "An aware organization timestamp is required."
        )
    return value.astimezone(timezone.utc)


def _code(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 10
        or value != value.strip()
        or any(char.isspace() for char in value)
    ):
        raise OrganizationLanguageValidationError(
            "A canonical catalog language code is required."
        )
    return value


@dataclass(frozen=True)
class OrganizationLanguageResult:
    tenant_id: UUID
    default_language: str
    updated_at: datetime
    changed: bool
    row_version: int


class ConstructionOrganizationLanguageAuthorizer:
    """Shared identity and permission policies, refreshed after lock waits."""

    def __init__(self, *, session, identity, access_resolver):
        if (
            session is None
            or getattr(identity, "session", None) is not session
            or getattr(
                getattr(access_resolver, "repository", None),
                "session", None,
            ) is not session
        ):
            raise ValueError(
                "Organization language authority requires the shared Platform session."
            )
        self.session = session
        self.identity = identity
        self.access_resolver = access_resolver

    async def require(self, *, actor):
        if (
            not isinstance(actor, ApiActorContext)
            or actor.status != "active"
            or not isinstance(actor.user_id, UUID)
            or not isinstance(actor.tenant_id, UUID)
        ):
            raise OrganizationLanguageDenied(
                "Organization settings are unavailable."
            )

        # Core SQL is used until audit. Refresh cached User/grant state.
        self.session.expire_all()
        try:
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
                raise OrganizationLanguageDenied(
                    "Organization settings are unavailable."
                )

            context = await self.access_resolver.resolve(actor=current)
            require_construction_settings_manage(
                actor=current,
                access_context=context,
            )
            require_company_profile_manage(
                actor=current,
                construction_access_context=context,
            )
            return current
        except (
            ApiIdentityAccessError,
            ConstructionTenantSettingsPermissionDeniedError,
            CompanyProfilePermissionDeniedError,
        ):
            raise OrganizationLanguageDenied(
                "Organization settings are unavailable."
            ) from None


class ConstructionOrganizationLanguageService:
    def __init__(
        self, *, session, repository, authorizer, events, now_provider=None,
    ):
        if (
            session is None
            or any(
                getattr(value, "session", None) is not session
                for value in (repository, authorizer, events)
            )
        ):
            raise ValueError(
                "Organization language, authority and audit require one session."
            )
        self.session = session
        self.repository = repository
        self.authorizer = authorizer
        self.events = events
        self.now_provider = now_provider or (
            lambda: datetime.now(timezone.utc)
        )

    async def set_default_for_actor(
        self, *, actor, language_code,
        expected_row_version, trace_id=None,
    ):
        # Check before taking ownership of the transaction.
        if any(
            bool(getattr(self.session, name))
            for name in ("new", "dirty", "deleted")
        ):
            raise OrganizationLanguageOperationError(
                "A clean Platform session is required."
            )

        try:
            language_code = _code(language_code)
            if type(expected_row_version) is not int or not 1 <= expected_row_version <= 2**63 - 1:
                raise OrganizationLanguageValidationError("Expected row version is invalid.")
            if trace_id is not None and (
                not isinstance(trace_id, str)
                or not trace_id.strip()
                or len(trace_id) > 255
            ):
                raise OrganizationLanguageValidationError(
                    "Audit trace is invalid."
                )

            current = await self.authorizer.require(actor=actor)
            if not await self.repository.lock_active_language(
                language_code=language_code
            ):
                raise OrganizationLanguageValidationError(
                    "The Platform language is inactive or missing."
                )

            state = await self.repository.lock_active_organization(
                tenant_id=current.tenant_id
            )
            current = await self.authorizer.require(actor=actor)
            if state is None or state.get("tenant_id") != current.tenant_id:
                raise OrganizationLanguageDenied(
                    "Organization settings are unavailable."
                )

            previous_language = _code(state["default_language"])
            previous_time = _utc(state["updated_at"])
            previous_version = state.get("row_version")
            if type(previous_version) is not int or not 1 <= previous_version <= 2**63 - 1:
                raise OrganizationLanguageOperationError("Organization row version is invalid.")
            if previous_version != expected_row_version:
                raise OrganizationLanguageConflict(
                    "Organization language was changed."
                )

            changed_at = _utc(self.now_provider())
            enabled_before = await self.repository.enable_language(
                tenant_id=current.tenant_id,
                language_code=language_code,
                as_of=changed_at,
            )
            current = await self.authorizer.require(actor=actor)

            if previous_language == language_code and enabled_before is True:
                result = OrganizationLanguageResult(
                    current.tenant_id, language_code, previous_time, False, previous_version,
                )
                await self.session.commit()
                return result

            if previous_version == 2**63 - 1:
                raise OrganizationLanguageOperationError("Organization row version is exhausted.")
            saved = await self.repository.replace_default(
                tenant_id=current.tenant_id,
                expected_language=previous_language,
                expected_row_version=previous_version,
                language_code=language_code,
                as_of=changed_at,
            )
            if saved is None:
                raise OrganizationLanguageConflict(
                    "Organization language was changed."
                )

            saved_time = _utc(saved["updated_at"])
            saved_version = saved.get("row_version")
            if (
                saved.get("tenant_id") != current.tenant_id
                or saved.get("default_language") != language_code
                or type(saved_version) is not int
                or saved_version != previous_version + 1
            ):
                raise OrganizationLanguageOperationError(
                    "Organization language result is invalid."
                )

            await self.events.create_event(
                event_type="construction_organization_language_updated",
                tenant_id=current.tenant_id,
                user_id=current.user_id,
                entity_type="tenant",
                entity_id=current.tenant_id,
                payload={
                    "field": "default_language",
                    "old_value": previous_language,
                    "new_value": language_code,
                    "enabled_for_organization_before": enabled_before,
                    "enabled_for_organization_after": True,
                    "previous_updated_at": previous_time.isoformat(),
                    "updated_at": saved_time.isoformat(),
                    "previous_row_version": previous_version,
                    "row_version": saved_version,
                },
                platform="api",
                trace_id=trace_id,
            )
            result = OrganizationLanguageResult(
                current.tenant_id, language_code, saved_time, True, saved_version,
            )
            await self.session.commit()
            return result
        except BaseException as exc:
            await self.session.rollback()
            if isinstance(exc, (
                OrganizationLanguageDenied,
                OrganizationLanguageValidationError,
                OrganizationLanguageConflict,
                OrganizationLanguageOperationError,
            )):
                raise
            if isinstance(exc, Exception):
                raise OrganizationLanguageOperationError(
                    "Organization language update failed."
                ) from None
            raise


def build_construction_organization_language_service(*, platform_session):
    from database.repositories.event import EventRepository
    from database.repositories.platform_organization_language import (
        PlatformOrganizationLanguageRepository,
    )
    from services.api_identity import ApiIdentityService
    from services.construction.construction_grants import (
        build_construction_access_context_resolver,
    )

    return ConstructionOrganizationLanguageService(
        session=platform_session,
        repository=PlatformOrganizationLanguageRepository(platform_session),
        authorizer=ConstructionOrganizationLanguageAuthorizer(
            session=platform_session,
            identity=ApiIdentityService(platform_session),
            access_resolver=build_construction_access_context_resolver(
                platform_session
            ),
        ),
        events=EventRepository(platform_session),
    )

# con_011_row_version_v1
