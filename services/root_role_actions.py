from datetime import datetime, timezone
from uuid import UUID

from database.models import (
    RootRecoveryAction,
    UserRoleMapping,
)
from database.repositories.root_recovery import (
    RootRecoveryRepository,
)
from services.root_recovery import (
    RootActionError,
)


class RootRoleActionExecutor:
    def __init__(
        self,
        repository: RootRecoveryRepository,
    ):
        self.repository = repository

    async def execute(
        self,
        *,
        action: RootRecoveryAction,
        root_identity_id: UUID,
        now: datetime,
    ) -> dict:
        if action.state != "confirmed":
            raise RootActionError(
                "Confirmed Root action required."
            )

        legacy_now = (
            now.astimezone(timezone.utc)
            .replace(tzinfo=None)
            if now.tzinfo
            else now
        )

        target_user = await (
            self.repository.get_target_user(
                tenant_id=action.tenant_id,
                user_id=action.target_user_id,
                for_update=True,
            )
        )

        if (
            not target_user
            or target_user.status != "active"
        ):
            raise RootActionError(
                "Target user not found."
            )

        if action.action_type == (
            "restore_super_admin"
        ):
            return await self._restore_super_admin(
                action=action,
                now=legacy_now,
            )

        if action.action_type == (
            "grant_administrative_role"
        ):
            return await (
                self._grant_administrative_role(
                    action=action,
                    now=legacy_now,
                )
            )

        if action.action_type == (
            "revoke_administrative_role"
        ):
            return await (
                self._revoke_administrative_role(
                    action=action,
                    root_identity_id=(
                        root_identity_id
                    ),
                    now=legacy_now,
                )
            )

        if action.action_type == (
            "change_regional_admin_scopes"
        ):
            return await self._change_admin_scopes(
                action=action,
                root_identity_id=root_identity_id,
                now=legacy_now,
            )

        raise RootActionError(
            "Unsupported Root action."
        )

    async def _activate_role(
        self,
        *,
        action: RootRecoveryAction,
        role: str,
        now: datetime,
    ) -> UserRoleMapping:
        role_mapping = await (
            self.repository.get_latest_user_role(
                tenant_id=action.tenant_id,
                user_id=action.target_user_id,
                role=role,
                for_update=True,
            )
        )

        if role_mapping:
            if role_mapping.status == "active":
                return role_mapping

            role_mapping.status = "active"
            role_mapping.tenant_id = (
                action.tenant_id
            )
            role_mapping.granted_by = None
            role_mapping.granted_at = now
            await self.repository.session.flush()
            return role_mapping

        return await self.repository.create_user_role(
            tenant_id=action.tenant_id,
            user_id=action.target_user_id,
            role=role,
            granted_at=now,
        )

    async def _resolve_scope_targets(
        self,
        action: RootRecoveryAction,
    ) -> tuple[dict, dict]:
        country_codes = action.action_payload.get(
            "country_codes",
            [],
        )
        language_codes = action.action_payload.get(
            "language_codes",
            [],
        )

        countries = await (
            self.repository.resolve_active_countries(
                country_codes
            )
        )
        languages = await (
            self.repository.resolve_active_languages(
                language_codes
            )
        )

        if set(countries) != set(country_codes):
            raise RootActionError(
                "Unknown or inactive country scope."
            )

        if set(languages) != set(
            language_codes
        ):
            raise RootActionError(
                "Unknown or inactive language scope."
            )

        return countries, languages

    async def _replace_admin_scopes(
        self,
        *,
        action: RootRecoveryAction,
        root_identity_id: UUID,
        role_mapping: UserRoleMapping,
        countries: dict,
        languages: dict,
        now: datetime,
    ) -> dict:
        active_scopes = await (
            self.repository.list_active_role_scopes(
                tenant_id=action.tenant_id,
                user_id=action.target_user_id,
                role="admin",
                for_update=True,
            )
        )

        for scope in active_scopes:
            scope.status = "revoked"
            scope.revoked_by = None
            scope.revoked_by_root_identity_id = (
                root_identity_id
            )
            scope.revoked_at = now

        if active_scopes:
            await self.repository.session.flush()

        created_scope_ids = []

        for country_code in sorted(countries):
            scope = await (
                self.repository.create_country_scope(
                    tenant_id=action.tenant_id,
                    user_role_id=role_mapping.id,
                    user_id=action.target_user_id,
                    root_identity_id=(
                        root_identity_id
                    ),
                    country_id=(
                        countries[country_code].id
                    ),
                    reason=action.reason,
                    created_at=now,
                )
            )
            created_scope_ids.append(str(scope.id))

        for language_code in sorted(languages):
            scope = await (
                self.repository.create_language_scope(
                    tenant_id=action.tenant_id,
                    user_role_id=role_mapping.id,
                    user_id=action.target_user_id,
                    root_identity_id=(
                        root_identity_id
                    ),
                    language_code=language_code,
                    reason=action.reason,
                    created_at=now,
                )
            )
            created_scope_ids.append(str(scope.id))

        return {
            "role_mapping_id": str(
                role_mapping.id
            ),
            "country_codes": sorted(countries),
            "language_codes": sorted(languages),
            "revoked_scopes": len(active_scopes),
            "created_scope_ids": (
                created_scope_ids
            ),
        }

    async def _restore_super_admin(
        self,
        *,
        action: RootRecoveryAction,
        now: datetime,
    ) -> dict:
        role_mapping = await self._activate_role(
            action=action,
            role="super_admin",
            now=now,
        )

        return {
            "role": "super_admin",
            "role_mapping_id": str(
                role_mapping.id
            ),
            "status": role_mapping.status,
        }

    async def _grant_administrative_role(
        self,
        *,
        action: RootRecoveryAction,
        now: datetime,
    ) -> dict:
        role = action.action_payload["role"]

        role_mapping = await self._activate_role(
            action=action,
            role=role,
            now=now,
        )

        return {
            "role": role,
            "role_mapping_id": str(
                role_mapping.id
            ),
            "status": role_mapping.status,
        }

    async def _change_admin_scopes(
        self,
        *,
        action: RootRecoveryAction,
        root_identity_id: UUID,
        now: datetime,
    ) -> dict:
        role_mapping = await (
            self.repository.get_latest_user_role(
                tenant_id=action.tenant_id,
                user_id=action.target_user_id,
                role="admin",
                for_update=True,
            )
        )

        if (
            not role_mapping
            or role_mapping.status != "active"
        ):
            raise RootActionError(
                "Active admin role not found."
            )

        countries, languages = (
            await self._resolve_scope_targets(
                action
            )
        )

        return await self._replace_admin_scopes(
            action=action,
            root_identity_id=root_identity_id,
            role_mapping=role_mapping,
            countries=countries,
            languages=languages,
            now=now,
        )

    async def _revoke_administrative_role(
        self,
        *,
        action: RootRecoveryAction,
        root_identity_id: UUID,
        now: datetime,
    ) -> dict:
        role = action.action_payload["role"]

        role_mapping = await (
            self.repository.get_latest_user_role(
                tenant_id=action.tenant_id,
                user_id=action.target_user_id,
                role=role,
                for_update=True,
            )
        )

        if (
            not role_mapping
            or role_mapping.status != "active"
        ):
            raise RootActionError(
                "Active administrative role "
                "not found."
            )

        if role == "super_admin":
            active_holders = await (
                self.repository
                .count_active_role_holders(
                    tenant_id=action.tenant_id,
                    role="super_admin",
                )
            )

            if active_holders <= 1:
                raise RootActionError(
                    "The last active Super Admin "
                    "cannot be revoked."
                )

        active_scopes = []

        if role == "admin":
            active_scopes = await (
                self.repository
                .list_active_role_scopes(
                    tenant_id=action.tenant_id,
                    user_id=action.target_user_id,
                    role="admin",
                    for_update=True,
                )
            )

        role_mapping.status = "revoked"

        for scope in active_scopes:
            scope.status = "revoked"
            scope.revoked_by = None
            scope.revoked_by_root_identity_id = (
                root_identity_id
            )
            scope.revoked_at = now

        await self.repository.session.flush()

        return {
            "role": role,
            "role_mapping_id": str(
                role_mapping.id
            ),
            "status": "revoked",
            "revoked_scopes": len(
                active_scopes
            ),
        }

