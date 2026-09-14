from dataclasses import dataclass, replace
from uuid import UUID

from database.repositories.event import (
    EventRepository,
)
from database.repositories.translation import (
    TranslationRepository,
)
from database.repositories.user import (
    UserRepository,
)
from services.translation import (
    TranslationError,
    TranslationService,
)
from services.user import UserService
from services.user_settings import (
    UserSettingsService,
    UserSettingsValidationError,
)


class ApiIdentityAccessError(Exception):
    pass


class ApiIdentityRoleError(Exception):
    pass


class ApiIdentityProfileError(Exception):
    pass


class ApiIdentityProfileValidationError(
    ApiIdentityProfileError
):
    pass


@dataclass(frozen=True)
class ApiRoleScopeContext:
    role: str
    scope_type: str
    scope_id: UUID | None
    scope_code: str | None


@dataclass(frozen=True)
class ApiActorContext:
    user_id: UUID
    tenant_id: UUID
    active_role: str | None
    roles: tuple[str, ...]
    language_code: str
    timezone: str | None
    status: str
    role_scopes: tuple[
        ApiRoleScopeContext,
        ...,
    ] = ()
    permissions: tuple[str, ...] = ()


class ApiIdentityService:
    def __init__(
        self,
        session,
        *,
        user_service=None,
        user_repository=None,
        event_repository=None,
        translation_service=None,
    ):
        self.session = session
        self.user_service = (
            user_service
            if user_service is not None
            else UserService(session)
        )
        self.user_repository = (
            user_repository
            if user_repository is not None
            else UserRepository(session)
        )
        self.event_repository = (
            event_repository
            if event_repository is not None
            else EventRepository(session)
        )
        self.translation_service = (
            translation_service
            if translation_service is not None
            else TranslationService(
                TranslationRepository(session)
            )
        )

    async def require_actor(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
    ) -> ApiActorContext:
        user = (
            await self.user_service
            .get_user_by_id(user_id)
        )

        if (
            user is None
            or user.id != user_id
            or user.tenant_id is None
            or user.tenant_id != tenant_id
            or user.status != "active"
        ):
            raise ApiIdentityAccessError(
                "API actor is not available."
            )

        active_roles = tuple(
            sorted(
                set(
                    await self.user_repository
                    .list_active_roles(
                        user_id,
                        tenant_id=tenant_id,
                    )
                )
            )
        )

        permission_codes = await (
            self.user_repository
            .list_active_permissions(
                roles=active_roles,
            )
        )
        permissions = tuple(
            sorted(
                {
                    str(code).strip()
                    for code in permission_codes
                    if str(code).strip()
                }
            )
        )

        scope_rows = await (
            self.user_repository
            .list_active_role_scopes(
                user_id=user_id,
                tenant_id=tenant_id,
                roles=active_roles,
            )
        )
        role_scopes = tuple(
            ApiRoleScopeContext(
                role=row.role,
                scope_type=row.scope_type,
                scope_id=row.scope_id,
                scope_code=row.scope_code,
            )
            for row in scope_rows
            if row.role in active_roles
        )

        active_role = (
            user.active_role
            if user.active_role
            in active_roles
            else None
        )

        return ApiActorContext(
            user_id=user.id,
            tenant_id=user.tenant_id,
            active_role=active_role,
            roles=active_roles,
            language_code=user.language_code,
            timezone=user.timezone,
            status=user.status,
            role_scopes=role_scopes,
            permissions=permissions,
        )

    async def switch_active_role(
        self,
        *,
        actor: ApiActorContext,
        role: str,
    ) -> ApiActorContext:
        normalized_role = (
            role.strip().lower()
            if isinstance(role, str)
            else ""
        )

        if (
            not normalized_role
            or normalized_role
            not in actor.roles
        ):
            raise ApiIdentityRoleError(
                "Requested role is not available."
            )

        try:
            updated_user = (
                await self.user_repository
                .set_active_role(
                    actor.user_id,
                    normalized_role,
                )
            )

            if (
                updated_user.id
                != actor.user_id
                or updated_user.tenant_id
                != actor.tenant_id
                or updated_user.active_role
                != normalized_role
            ):
                raise ApiIdentityRoleError(
                    "Role switch result is invalid."
                )

            await self.event_repository.create_event(
                event_type="role_switched",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="user",
                entity_id=actor.user_id,
                payload={
                    "active_role": normalized_role,
                    "available_roles": list(
                        actor.roles
                    ),
                },
                platform="api",
            )

            await self.session.commit()
        except ValueError:
            await self.session.rollback()
            raise ApiIdentityRoleError(
                "Requested role is not available."
            ) from None
        except ApiIdentityRoleError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise

        return replace(
            actor,
            active_role=normalized_role,
        )

    async def update_profile(
        self,
        *,
        actor: ApiActorContext,
        language_code: str,
    ) -> ApiActorContext:
        try:
            normalized_language = (
                UserSettingsService
                .validate_language_code(
                    language_code
                )
            )
        except UserSettingsValidationError:
            raise (
                ApiIdentityProfileValidationError(
                    "Profile language "
                    "is not supported."
                )
            ) from None

        try:
            settings = await (
                self.translation_service
                .update_interface_language(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                    language_code=normalized_language,
                    source="api_profile",
                    platform="api",
                )
            )
        except TranslationError:
            raise ApiIdentityProfileError(
                "Unable to update API profile."
            ) from None

        return replace(
            actor,
            language_code=(
                settings.interface_language
            ),
        )
