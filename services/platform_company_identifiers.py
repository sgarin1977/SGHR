from uuid import UUID

import pycountry
from sqlalchemy.exc import IntegrityError

from services.platform_company_profile_access import (
    CompanyProfilePermissionDeniedError,
    require_company_profile_manage,
)


class CompanyIdentifierValidationError(ValueError):
    pass


def normalize_company_identifier_country_code(value: str) -> str:
    if not isinstance(value, str):
        raise CompanyIdentifierValidationError(
            "Country code is invalid."
        )
    code = value.strip().upper()
    if pycountry.countries.get(alpha_2=code) is None:
        raise CompanyIdentifierValidationError(
            "Country code is invalid."
        )
    return code


def normalize_company_identifier_fields(
    *,
    identifier_type: str,
    identifier_value: str,
) -> dict[str, str]:
    if not isinstance(identifier_type, str):
        raise CompanyIdentifierValidationError(
            "Identifier type is invalid."
        )
    normalized_type = identifier_type.strip().lower()
    if not normalized_type or len(normalized_type) > 50:
        raise CompanyIdentifierValidationError(
            "Identifier type is invalid."
        )

    if not isinstance(identifier_value, str):
        raise CompanyIdentifierValidationError(
            "Identifier value is invalid."
        )
    normalized_value = "".join(
        character
        for character in identifier_value.strip().upper()
        if not character.isspace()
    )
    if (
        not normalized_value
        or len(identifier_value) > 100
    ):
        raise CompanyIdentifierValidationError(
            "Identifier value is invalid."
        )

    return {
        "identifier_type": normalized_type,
        "identifier_value": identifier_value,
        "identifier_value_normalized": normalized_value,
    }


def _is_identifier_unique_conflict(error: IntegrityError) -> bool:
    confirmed_indexes = {
        "uq_tenant_company_identifiers_normalized",
        "uq_tenant_company_identifiers_primary_scope",
    }
    pending = [error.orig]
    seen = set()
    has_unique_sqlstate = False
    has_confirmed_index = False

    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        has_unique_sqlstate = has_unique_sqlstate or (
            getattr(current, "sqlstate", None) == "23505"
            or getattr(current, "pgcode", None) == "23505"
        )
        has_confirmed_index = has_confirmed_index or (
            getattr(current, "constraint_name", None) in confirmed_indexes
        )
        pending.append(getattr(current, "__cause__", None))
        pending.append(getattr(current, "__context__", None))

    return has_unique_sqlstate and has_confirmed_index


class CompanyIdentifierOperationError(Exception):
    pass


class CompanyIdentifierProfileNotFoundError(
    CompanyIdentifierOperationError
):
    status_code = 404
    code = "company_profile_not_found"


class CompanyIdentifierNotFoundError(CompanyIdentifierOperationError):
    status_code = 404
    code = "company_identifier_not_found"


class CompanyIdentifierConflictError(CompanyIdentifierOperationError):
    status_code = 409
    code = "company_identifier_conflict"


class TenantCompanyIdentifierService:
    def __init__(self, *, session, profile_repository,
                 identifier_repository, event_repository):
        self.session = session
        self.profile_repository = profile_repository
        self.identifier_repository = identifier_repository
        self.event_repository = event_repository

    async def list_identifiers(self, *, actor):
        if (
            actor is None
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or getattr(actor, "status", None) != "active"
        ):
            raise CompanyProfilePermissionDeniedError(
                "Company identifier access is not available."
            )

        profile = await self.profile_repository.get_for_tenant(
            tenant_id=actor.tenant_id,
        )
        if profile is None:
            return []
        if (
            profile.tenant_id != actor.tenant_id
            or not isinstance(profile.id, UUID)
        ):
            raise CompanyProfilePermissionDeniedError(
                "Company identifier access is not available."
            )

        identifiers = await self.identifier_repository.list_for_tenant(
            tenant_id=actor.tenant_id,
        )
        if any(
            item.tenant_company_profile_id != profile.id
            for item in identifiers
        ):
            raise CompanyProfilePermissionDeniedError(
                "Company identifier access is not available."
            )
        return list(identifiers)

    async def create_identifier(
        self, *, actor, country_code, identifier_type,
        identifier_value, is_primary=False,
        construction_access_context=None, trace_id=None,
    ):
        require_company_profile_manage(
            actor=actor,
            construction_access_context=construction_access_context,
        )
        if (
            not isinstance(actor.tenant_id, UUID)
            or not isinstance(actor.user_id, UUID)
            or not isinstance(is_primary, bool)
        ):
            raise CompanyIdentifierValidationError(
                "Identifier context is invalid."
            )
        code = normalize_company_identifier_country_code(country_code)
        fields = normalize_company_identifier_fields(
            identifier_type=identifier_type,
            identifier_value=identifier_value,
        )
        try:
            profile = await self.profile_repository.get_for_tenant_for_update(
                tenant_id=actor.tenant_id
            )
            if profile is None or profile.tenant_id != actor.tenant_id:
                raise CompanyIdentifierProfileNotFoundError(
                    "Company profile is not available."
                )
            identifier = await self.identifier_repository.create_identifier(
                profile=profile, country_code=code,
                is_primary=is_primary, **fields,
            )
            if identifier.tenant_company_profile_id != profile.id:
                raise CompanyIdentifierOperationError(
                    "Identifier scope is invalid."
                )
            await self.event_repository.create_event(
                event_type="platform_company_identifier_created",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="tenant_company_identifier",
                entity_id=identifier.id,
                payload={
                    "operation": "create",
                    "country_code": code,
                    "identifier_type": fields["identifier_type"],
                    "is_primary": is_primary,
                },
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return identifier
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, CompanyIdentifierOperationError):
                raise
            if (
                isinstance(exc, IntegrityError)
                and _is_identifier_unique_conflict(exc)
            ):
                raise CompanyIdentifierConflictError(
                    "Company identifier conflicts with an existing record."
                ) from exc
            raise CompanyIdentifierOperationError(
                "Identifier operation failed."
            ) from exc


    async def replace_identifier(
        self, *, actor, identifier_id, country_code,
        identifier_type, identifier_value, is_primary,
        construction_access_context=None, trace_id=None,
    ):
        require_company_profile_manage(
            actor=actor,
            construction_access_context=construction_access_context,
        )
        if (
            not isinstance(actor.tenant_id, UUID)
            or not isinstance(actor.user_id, UUID)
            or not isinstance(identifier_id, UUID)
            or not isinstance(is_primary, bool)
        ):
            raise CompanyIdentifierValidationError(
                "Identifier context is invalid."
            )
        code = normalize_company_identifier_country_code(country_code)
        fields = normalize_company_identifier_fields(
            identifier_type=identifier_type,
            identifier_value=identifier_value,
        )
        try:
            profile = await self.profile_repository.get_for_tenant_for_update(
                tenant_id=actor.tenant_id
            )
            if profile is None or profile.tenant_id != actor.tenant_id:
                raise CompanyIdentifierProfileNotFoundError(
                    "Company profile is not available."
                )
            old = await self.identifier_repository.get_for_tenant_for_update(
                tenant_id=actor.tenant_id,
                identifier_id=identifier_id,
            )
            if (
                old is None
                or old.tenant_company_profile_id != profile.id
            ):
                raise CompanyIdentifierNotFoundError(
                    "Identifier is not available."
                )
            changes = [
                {
                    "field": field,
                    "old_value": getattr(old, field),
                    "new_value": value,
                }
                for field, value in (
                    ("country_code", code),
                    ("identifier_type", fields["identifier_type"]),
                    ("identifier_value", fields["identifier_value"]),
                    ("is_primary", is_primary),
                )
            ]
            await self.identifier_repository.delete_locked_identifier(
                identifier=old
            )
            new = await self.identifier_repository.create_identifier(
                profile=profile,
                country_code=code,
                is_primary=is_primary,
                **fields,
            )
            if new.tenant_company_profile_id != profile.id:
                raise CompanyIdentifierOperationError(
                    "Identifier scope is invalid."
                )
            await self.event_repository.create_event(
                event_type="platform_company_identifier_replaced",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="tenant_company_identifier",
                entity_id=new.id,
                payload={
                    "operation": "replace",
                    "old_identifier_id": str(old.id),
                    "changes": changes,
                },
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return new
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, CompanyIdentifierOperationError):
                raise
            if (
                isinstance(exc, IntegrityError)
                and _is_identifier_unique_conflict(exc)
            ):
                raise CompanyIdentifierConflictError(
                    "Company identifier conflicts with an existing record."
                ) from exc
            raise CompanyIdentifierOperationError(
                "Identifier operation failed."
            ) from exc


    async def delete_identifier(
        self, *, actor, identifier_id,
        construction_access_context=None, trace_id=None,
    ):
        require_company_profile_manage(
            actor=actor,
            construction_access_context=construction_access_context,
        )
        if (
            not isinstance(actor.tenant_id, UUID)
            or not isinstance(actor.user_id, UUID)
            or not isinstance(identifier_id, UUID)
        ):
            raise CompanyIdentifierValidationError(
                "Identifier context is invalid."
            )
        try:
            profile = await self.profile_repository.get_for_tenant_for_update(
                tenant_id=actor.tenant_id
            )
            if profile is None or profile.tenant_id != actor.tenant_id:
                raise CompanyIdentifierProfileNotFoundError(
                    "Company profile is not available."
                )
            old = await self.identifier_repository.get_for_tenant_for_update(
                tenant_id=actor.tenant_id,
                identifier_id=identifier_id,
            )
            if (
                old is None
                or old.tenant_company_profile_id != profile.id
            ):
                raise CompanyIdentifierNotFoundError(
                    "Identifier is not available."
                )
            changes = [
                {
                    "field": field,
                    "old_value": getattr(old, field),
                    "new_value": None,
                }
                for field in (
                    "country_code", "identifier_type",
                    "identifier_value", "is_primary",
                )
            ]
            await self.identifier_repository.delete_locked_identifier(
                identifier=old
            )
            await self.event_repository.create_event(
                event_type="platform_company_identifier_deleted",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="tenant_company_identifier",
                entity_id=old.id,
                payload={
                    "operation": "delete",
                    "changes": changes,
                },
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, CompanyIdentifierOperationError):
                raise
            raise CompanyIdentifierOperationError(
                "Identifier operation failed."
            ) from exc


def build_tenant_company_identifier_service(session):
    from database.repositories.company_profiles import (
        TenantCompanyIdentifierRepository,
        TenantCompanyProfileRepository,
    )
    from database.repositories.event import EventRepository

    return TenantCompanyIdentifierService(
        session=session,
        profile_repository=TenantCompanyProfileRepository(session),
        identifier_repository=TenantCompanyIdentifierRepository(session),
        event_repository=EventRepository(session),
    )
