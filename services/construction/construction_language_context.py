"""CON-011 language context, using shared identity and Platform catalogs."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID
from services.api_identity import ApiActorContext, ApiIdentityAccessError


class ConstructionLanguageAccessError(Exception):
    pass


class ConstructionLanguageConfigurationError(Exception):
    pass


class ConstructionLanguageOperationError(Exception):
    pass


@dataclass(frozen=True)
class ConstructionLanguageOption:
    code: str
    name: str
    native_name: str | None
    label: str
    enabled_for_organization: bool


@dataclass(frozen=True)
class ConstructionLanguageContext:
    tenant_id: UUID
    user_id: UUID
    interface_language: str
    organization_default_language: str
    organization_updated_at: datetime
    row_version: int
    languages: tuple[ConstructionLanguageOption, ...]


class ConstructionLanguageContextService:
    def __init__(self, *, session, repository, identity):
        if (session is None or not hasattr(session, "no_autoflush")
                or getattr(repository, "session", None) is not session
                or getattr(identity, "session", None) is not session):
            raise ValueError("Language context requires shared-session Platform reads.")
        self.session, self.repository, self.identity = session, repository, identity

    async def _current(self, actor):
        if (not isinstance(actor, ApiActorContext) or actor.status != "active"
                or not isinstance(actor.user_id, UUID) or not isinstance(actor.tenant_id, UUID)):
            raise ConstructionLanguageAccessError("Language context is unavailable.")
        current = await self.identity.require_actor(user_id=actor.user_id, tenant_id=actor.tenant_id)
        if (not isinstance(current, ApiActorContext) or current.status != "active"
                or current.user_id != actor.user_id or current.tenant_id != actor.tenant_id):
            raise ConstructionLanguageAccessError("Language context is unavailable.")
        return current

    @staticmethod
    def _options(rows):
        options, seen = [], set()
        for row in rows:
            code, name = row.get("code"), row.get("name")
            native, enabled = row.get("native_name"), row.get("enabled_for_organization")
            if (not isinstance(code, str) or not code or len(code) > 10
                    or code != code.strip() or code in seen
                    or not isinstance(name, str) or not name.strip()
                    or native is not None and not isinstance(native, str)
                    or type(enabled) is not bool):
                raise ConstructionLanguageConfigurationError("Platform language catalog is invalid.")
            seen.add(code)
            label = native if native is not None and native.strip() else name
            options.append(ConstructionLanguageOption(code, name, native, label, enabled))
        return tuple(options)

    async def read_for_actor(self, *, actor):
        try:
            with self.session.no_autoflush:
                current = await self._current(actor)
                tenant = await self.repository.get_tenant_language(tenant_id=current.tenant_id)
                if tenant is None or tenant.get("tenant_id") != current.tenant_id:
                    raise ConstructionLanguageAccessError("Language context is unavailable.")
                options = self._options(
                    await self.repository.list_language_catalog(tenant_id=current.tenant_id)
                )
                # Refresh identity/membership; never use request language or
                # organization defaults as a substitute for the user profile.
                current = await self._current(actor)
                by_code = {option.code: option for option in options}
                interface = current.language_code
                default = tenant.get("default_language")
                if (not isinstance(interface, str) or interface not in by_code
                        or not isinstance(default, str) or default not in by_code
                        or not by_code[default].enabled_for_organization):
                    raise ConstructionLanguageConfigurationError("Language configuration is required.")
                updated_at = tenant.get("updated_at")
                if (not isinstance(updated_at, datetime) or updated_at.tzinfo is None
                        or updated_at.utcoffset() is None):
                    raise ConstructionLanguageConfigurationError("Organization timestamp is required.")
                row_version = tenant.get("row_version")
                if type(row_version) is not int or not 1 <= row_version <= 2**63 - 1:
                    raise ConstructionLanguageConfigurationError("Organization row version is required.")
                return ConstructionLanguageContext(
                    current.tenant_id, current.user_id, interface, default,
                    updated_at.astimezone(timezone.utc), row_version, options)
        except ApiIdentityAccessError:
            raise ConstructionLanguageAccessError("Language context is unavailable.") from None
        except (ConstructionLanguageAccessError, ConstructionLanguageConfigurationError):
            raise
        except Exception:
            raise ConstructionLanguageOperationError("Language context read failed.") from None


def build_construction_language_context_service(*, platform_session):
    from database.repositories.platform_localization import PlatformLocalizationRepository
    from services.api_identity import ApiIdentityService
    return ConstructionLanguageContextService(
        session=platform_session,
        repository=PlatformLocalizationRepository(platform_session),
        identity=ApiIdentityService(platform_session))

# con_011_organization_language_http_v1

# con_011_row_version_v1
