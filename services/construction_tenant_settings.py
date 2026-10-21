def resolve_construction_contact_fields(
    *,
    profile,
    settings,
) -> dict[str, str | None]:
    resolved = {}

    for field in ("phone", "email", "website"):
        override = (
            getattr(settings, f"{field}_override", None)
            if settings is not None
            else None
        )

        if override is not None:
            if not isinstance(override, str):
                raise ValueError(
                    "Construction contact override is invalid."
                )
            override = override.strip() or None

        resolved[field] = (
            override
            if override is not None
            else (
                getattr(profile, field, None)
                if profile is not None
                else None
            )
        )

    return resolved


class ConstructionTenantSettingsScopeError(Exception):
    pass


def _is_construction_settings_pk_violation(error):
    pending = [getattr(error, "orig", None)]
    seen = set()
    has_unique_state = False
    has_settings_pk = False

    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        has_unique_state |= (
            getattr(current, "sqlstate", None) == "23505"
            or getattr(current, "pgcode", None) == "23505"
        )
        has_settings_pk |= (
            getattr(current, "constraint_name", None)
            == "construction_tenant_settings_pkey"
        )
        pending.append(getattr(current, "orig", None))
        pending.append(getattr(current, "__cause__", None))
        pending.append(getattr(current, "__context__", None))

    return has_unique_state and has_settings_pk


class ConstructionTenantSettingsConflictError(Exception):
    pass


class ConstructionTenantSettingsOperationError(Exception):
    pass


class ConstructionTenantSettingsNotFoundError(Exception):
    pass


class ConstructionTenantSettingsRowVersionConflictError(Exception):
    pass


class ConstructionTenantSettingsService:
    def __init__(
        self, *, settings_reader, session=None,
        event_repository=None, file_repository=None,
        profile_reader=None,
    ):
        self.settings_reader = settings_reader
        self.profile_reader = profile_reader
        self.session = session
        self.event_repository = event_repository
        self.file_repository = file_repository

    async def get_effective_for_actor(
        self, *, actor, platform_display_name,
        tenant_ui_name, platform_ui_accent_color,
    ):
        settings = await self.get_for_actor(actor=actor)
        if self.profile_reader is None:
            raise ConstructionTenantSettingsScopeError(
                "Company profile reader is not configured."
            )

        profile = await self.profile_reader.get_for_tenant(
            tenant_id=actor.tenant_id,
        )
        if (
            profile is not None
            and getattr(profile, "tenant_id", None) != actor.tenant_id
        ):
            raise ConstructionTenantSettingsScopeError(
                "Company profile is not available."
            )

        return resolve_construction_company_presentation(
            profile=profile,
            settings=settings,
            platform_display_name=platform_display_name,
            tenant_ui_name=tenant_ui_name,
            platform_ui_accent_color=platform_ui_accent_color,
        )

    async def get_for_actor(self, *, actor):
        if actor.status != "active":
            raise ConstructionTenantSettingsScopeError(
                "Construction settings are not available."
            )

        settings = await self.settings_reader.get_for_tenant(
            tenant_id=actor.tenant_id
        )
        if settings is None:
            return None

        if getattr(settings, "tenant_id", None) != actor.tenant_id:
            raise ConstructionTenantSettingsScopeError(
                "Construction settings are not available."
            )

        return settings


    async def create_for_actor(
        self, *, actor, access_context, values, trace_id=None
    ):
        from uuid import UUID
        from database.repositories.files import FileStorageObjectNotFoundError
        from services.platform_company_profiles import (
            CompanyProfileValidationError, validate_company_logo_file,
        )
        require_construction_settings_manage(
            actor=actor, access_context=access_context
        )
        allowed = set(CONSTRUCTION_TEXT_OVERRIDE_LIMITS) | {
            "logo_file_id_override"
        }
        if not isinstance(values, dict) or not set(values).issubset(allowed):
            raise ValueError("Invalid Construction settings fields.")
        logo_file_id = values.get("logo_file_id_override")
        if logo_file_id is not None:
            if not isinstance(logo_file_id, UUID):
                raise ValueError("Invalid Construction logo file ID.")
            if self.file_repository is None:
                raise ConstructionTenantSettingsOperationError(
                    "Files reader is not configured."
                )

        normalized = normalize_construction_text_overrides(
            **{k: v for k, v in values.items()
               if k != "logo_file_id_override"}
        )
        if "logo_file_id_override" in values:
            normalized["logo_file_id_override"] = logo_file_id
        if self.session is None or self.event_repository is None:
            raise ConstructionTenantSettingsOperationError(
                "Construction settings writer is not configured."
            )

        try:
            existing = await self.settings_reader.get_for_tenant(
                tenant_id=actor.tenant_id
            )
            if existing is not None:
                raise ConstructionTenantSettingsConflictError(
                    "Construction settings already exist."
                )
            if logo_file_id is not None:
                logo = await self.file_repository.get_organization_downloadable_file(
                    tenant_id=actor.tenant_id, file_id=logo_file_id
                )
                validate_company_logo_file(
                    storage_object=logo, tenant_id=actor.tenant_id
                )
            settings = await self.settings_reader.create_settings(
                tenant_id=actor.tenant_id, values=normalized
            )
            if settings.tenant_id != actor.tenant_id:
                raise ValueError("Construction settings tenant mismatch.")
            await self.event_repository.create_event(
                event_type="construction_tenant_settings_created",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="construction_tenant_setting",
                entity_id=actor.tenant_id,
                payload={"operation": "create", "changes": []},
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return settings
        except (FileStorageObjectNotFoundError, CompanyProfileValidationError) as exc:
            await self.session.rollback()
            raise ValueError("Construction logo file is not available.") from exc
        except ConstructionTenantSettingsConflictError:
            await self.session.rollback()
            raise
        except Exception as exc:
            await self.session.rollback()
            from sqlalchemy.exc import IntegrityError

            if (
                isinstance(exc, IntegrityError)
                and _is_construction_settings_pk_violation(exc)
            ):
                raise ConstructionTenantSettingsConflictError(
                    "Construction settings already exist."
                ) from exc
            raise ConstructionTenantSettingsOperationError(
                "Construction settings operation failed."
            ) from exc


    async def update_for_actor(
        self, *, actor, access_context, expected_row_version,
        changes, trace_id=None,
    ):
        from sqlalchemy.orm.exc import StaleDataError
        from uuid import UUID
        from database.repositories.files import FileStorageObjectNotFoundError
        from services.platform_company_profiles import (
            CompanyProfileValidationError, validate_company_logo_file,
        )

        require_construction_settings_manage(
            actor=actor, access_context=access_context
        )
        if (
            isinstance(expected_row_version, bool)
            or not isinstance(expected_row_version, int)
            or expected_row_version <= 0
        ):
            raise ValueError("Expected row version is invalid.")
        allowed = set(CONSTRUCTION_TEXT_OVERRIDE_LIMITS) | {
            "logo_file_id_override"
        }
        if (
            not isinstance(changes, dict)
            or not changes
            or not set(changes).issubset(allowed)
        ):
            raise ValueError("Invalid Construction settings changes.")
        logo_file_id = changes.get("logo_file_id_override")
        if logo_file_id is not None:
            if not isinstance(logo_file_id, UUID):
                raise ValueError("Invalid Construction logo file ID.")
            if self.file_repository is None:
                raise ConstructionTenantSettingsOperationError(
                    "Files reader is not configured."
                )

        normalized = normalize_construction_text_overrides(
            **{k: v for k, v in changes.items()
               if k != "logo_file_id_override"}
        )
        if "logo_file_id_override" in changes:
            normalized["logo_file_id_override"] = logo_file_id
        if self.session is None or self.event_repository is None:
            raise ConstructionTenantSettingsOperationError(
                "Construction settings writer is not configured."
            )

        try:
            settings = await self.settings_reader.get_for_tenant_for_update(
                tenant_id=actor.tenant_id
            )
            if settings is None or settings.tenant_id != actor.tenant_id:
                raise ConstructionTenantSettingsNotFoundError(
                    "Construction settings are not available."
                )
            if settings.row_version != expected_row_version:
                raise ConstructionTenantSettingsRowVersionConflictError(
                    "Construction settings version is stale."
                )

            old = {name: getattr(settings, name) for name in normalized}
            changed = {
                name: value for name, value in normalized.items()
                if old[name] != value
            }
            if not changed:
                await self.session.commit()
                return settings

            if changed.get("logo_file_id_override") is not None:
                logo = await self.file_repository.get_organization_downloadable_file(
                    tenant_id=actor.tenant_id,
                    file_id=changed["logo_file_id_override"],
                )
                validate_company_logo_file(
                    storage_object=logo, tenant_id=actor.tenant_id
                )

            updated = await self.settings_reader.update_settings(
                settings=settings, values=changed
            )
            if updated.tenant_id != actor.tenant_id:
                raise ConstructionTenantSettingsOperationError(
                    "Construction settings tenant mismatch."
                )
            audit_changes = [
                {
                    "field": name,
                    "old_value": (
                        str(old[name])
                        if name == "logo_file_id_override"
                        and old[name] is not None else old[name]
                    ),
                    "new_value": (
                        str(value)
                        if name == "logo_file_id_override"
                        and value is not None else value
                    ),
                }
                for name, value in changed.items()
            ]
            await self.event_repository.create_event(
                event_type="construction_tenant_settings_updated",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="construction_tenant_setting",
                entity_id=actor.tenant_id,
                payload={"operation": "update", "changes": audit_changes},
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return updated
        except (ConstructionTenantSettingsNotFoundError,
                ConstructionTenantSettingsRowVersionConflictError):
            await self.session.rollback()
            raise
        except (FileStorageObjectNotFoundError, CompanyProfileValidationError) as exc:
            await self.session.rollback()
            raise ValueError("Construction logo file is not available.") from exc
        except StaleDataError as exc:
            await self.session.rollback()
            raise ConstructionTenantSettingsRowVersionConflictError(
                "Construction settings version is stale."
            ) from exc
        except Exception as exc:
            await self.session.rollback()
            raise ConstructionTenantSettingsOperationError(
                "Construction settings operation failed."
            ) from exc


def resolve_construction_logo_file_id(*, profile, settings):
    override = (
        getattr(settings, "logo_file_id_override", None)
        if settings is not None
        else None
    )
    if override is not None:
        return override

    return (
        getattr(profile, "logo_file_id", None)
        if profile is not None
        else None
    )


def resolve_construction_document_texts(*, settings):
    resolved = {}
    for field in ("document_header_text", "document_footer_text"):
        value = (
            getattr(settings, field, None)
            if settings is not None
            else None
        )
        if value is not None and not isinstance(value, str):
            raise ValueError("Construction document text is invalid.")
        resolved[field] = (
            value if value is not None and value.strip() else None
        )
    return resolved


def resolve_construction_accent_color(
    *,
    settings,
    platform_ui_accent_color,
):
    import re

    override = (
        getattr(settings, "accent_color", None)
        if settings is not None
        else None
    )
    if override is None:
        return platform_ui_accent_color
    if not isinstance(override, str):
        raise ValueError("Construction accent color is invalid.")

    override = override.strip()
    if not override:
        return platform_ui_accent_color
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", override) is None:
        raise ValueError("Construction accent color is invalid.")
    return override


CONSTRUCTION_TEXT_OVERRIDE_LIMITS = {
    "display_name_override": 255,
    "phone_override": 50,
    "email_override": 320,
    "website_override": 2048,
    "document_header_text": None,
    "document_footer_text": None,
    "accent_color": 7,
}


def normalize_construction_text_overrides(**values):
    import re

    normalized = {}
    for field, value in values.items():
        if field not in CONSTRUCTION_TEXT_OVERRIDE_LIMITS:
            raise ValueError("Construction override field is invalid.")
        if value is not None and not isinstance(value, str):
            raise ValueError("Construction override value is invalid.")

        if value is not None and not value.strip():
            value = None

        limit = CONSTRUCTION_TEXT_OVERRIDE_LIMITS[field]
        if value is not None and limit is not None:
            if len(value) > limit:
                raise ValueError("Construction override value is too long.")

        if (
            field == "accent_color"
            and value is not None
            and re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is None
        ):
            raise ValueError("Construction accent color is invalid.")

        normalized[field] = value

    return normalized


def resolve_construction_display_name(
    *,
    settings,
    platform_display_name,
    tenant_ui_name,
):
    override = (
        getattr(settings, "display_name_override", None)
        if settings is not None
        else None
    )
    override = normalize_construction_text_overrides(
        display_name_override=override
    )["display_name_override"]
    if override is not None:
        return override

    for value in (platform_display_name, tenant_ui_name):
        if value is not None and not isinstance(value, str):
            raise ValueError("Construction display name is invalid.")
        if value is not None and value.strip():
            return value

    return None


CONSTRUCTION_SETTINGS_MANAGE_PERMISSION = "construction.settings.manage"




class ConstructionTenantSettingsPermissionDeniedError(Exception):
    pass


def require_construction_settings_manage(*, actor, access_context):
    from uuid import UUID
    from services.construction_permissions import (
        ConstructionAccessContext, ConstructionPermissionDeniedError,
        ConstructionScopeDeniedError, require_construction_access,
    )

    if (
        getattr(actor, "status", None) != "active"
        or not isinstance(getattr(actor, "user_id", None), UUID)
        or not isinstance(getattr(actor, "tenant_id", None), UUID)
        or not isinstance(access_context, ConstructionAccessContext)
    ):
        raise ConstructionTenantSettingsPermissionDeniedError(
            "Construction settings management is not available."
        )
    try:
        require_construction_access(
            actor=actor, access_context=access_context,
            permission_code=CONSTRUCTION_SETTINGS_MANAGE_PERMISSION,
        )
    except (ConstructionPermissionDeniedError, ConstructionScopeDeniedError):
        raise ConstructionTenantSettingsPermissionDeniedError(
            "Construction settings management is not available."
        ) from None

def build_construction_tenant_settings_service(
    *, construction_session, platform_session
):
    from database.repositories.construction_tenant_settings import (
        ConstructionTenantSettingsRepository,
    )
    from database.repositories.company_profiles import (
        TenantCompanyProfileRepository,
    )
    from database.repositories.event import EventRepository
    from database.repositories.files import FileRepository

    if (
        construction_session is None
        or platform_session is None
        or construction_session is platform_session
    ):
        raise ValueError(
            "Construction and Platform sessions must be separate."
        )

    return ConstructionTenantSettingsService(
        settings_reader=ConstructionTenantSettingsRepository(
            construction_session
        ),
        session=construction_session,
        event_repository=EventRepository(construction_session),
        file_repository=FileRepository(platform_session),
        profile_reader=TenantCompanyProfileRepository(construction_session),
    )

def resolve_construction_company_presentation(
    *,
    profile,
    settings,
    platform_display_name,
    tenant_ui_name,
    platform_ui_accent_color,
):
    contacts = resolve_construction_contact_fields(
        profile=profile, settings=settings,
    )
    document_texts = resolve_construction_document_texts(
        settings=settings,
    )
    return {
        "display_name": resolve_construction_display_name(
            settings=settings,
            platform_display_name=platform_display_name,
            tenant_ui_name=tenant_ui_name,
        ),
        **contacts,
        "logo_file_id": resolve_construction_logo_file_id(
            profile=profile, settings=settings,
        ),
        **document_texts,
        "accent_color": resolve_construction_accent_color(
            settings=settings,
            platform_ui_accent_color=platform_ui_accent_color,
        ),
    }
