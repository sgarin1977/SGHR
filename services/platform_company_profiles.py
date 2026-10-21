from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from email_validator import EmailNotValidError, validate_email
import phonenumbers
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from database.repositories.event import EventRepository
from database.repositories.files import (
    FileRepository,
    FileStorageObjectNotFoundError,
)
from services.files import FileUploadValidationError, validate_file_upload
from services.platform_company_profile_access import require_company_profile_manage


_HTTP_URL = TypeAdapter(AnyHttpUrl)



def _is_tenant_profile_unique_violation(error: IntegrityError) -> bool:
    pending = [error.orig]
    seen = set()
    has_unique_sqlstate = False
    has_tenant_constraint = False

    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))

        has_unique_sqlstate = has_unique_sqlstate or (
            getattr(current, "sqlstate", None) == "23505"
            or getattr(current, "pgcode", None) == "23505"
        )
        has_tenant_constraint = has_tenant_constraint or (
            getattr(current, "constraint_name", None)
            == "uq_tenant_company_profiles_tenant_id"
        )

        pending.append(getattr(current, "__cause__", None))
        pending.append(getattr(current, "__context__", None))

    return has_unique_sqlstate and has_tenant_constraint


class CompanyProfileValidationError(ValueError):
    pass


def validate_company_logo_file(
    *,
    storage_object,
    tenant_id: UUID,
) -> UUID:
    if (
        not isinstance(tenant_id, UUID)
        or not isinstance(getattr(storage_object, "id", None), UUID)
        or getattr(storage_object, "tenant_id", None) != tenant_id
        or getattr(storage_object, "entity_type", None) != "organization"
        or getattr(storage_object, "entity_id", None) != tenant_id
        or getattr(storage_object, "status", None) != "ready"
        or getattr(storage_object, "antivirus_status", None)
        not in {"not_scanned", "clean"}
        or getattr(storage_object, "visibility_scope", None) != "private"
        or getattr(storage_object, "public_url", None) is not None
    ):
        raise CompanyProfileValidationError(
            "Company logo file is not available."
        )

    storage_path = getattr(storage_object, "storage_path", None)
    if not isinstance(storage_path, str) or not storage_path:
        raise CompanyProfileValidationError(
            "Company logo file is not available."
        )

    try:
        upload = validate_file_upload(
            object_type="organization",
            filename=Path(storage_path).name,
            mime_type=getattr(storage_object, "mime_type", None),
            size_bytes=getattr(storage_object, "size_bytes", None),
        )
    except FileUploadValidationError as exc:
        raise CompanyProfileValidationError(
            "Company logo file is not valid."
        ) from exc

    if (
        upload.file_type != "image"
        or getattr(storage_object, "file_type", None) != "image"
    ):
        raise CompanyProfileValidationError(
            "Company logo must be an image."
        )
    return storage_object.id


def normalize_company_profile_fields(
    *,
    legal_name: str,
    email: str | None,
    website: str | None,
    phone: str | None,
) -> dict[str, str | None]:
    if (
        not isinstance(legal_name, str)
        or not legal_name.strip()
        or len(legal_name) > 255
    ):
        raise CompanyProfileValidationError(
            "Legal name is invalid."
        )

    normalized_email = None
    if email is not None:
        if not isinstance(email, str):
            raise CompanyProfileValidationError(
                "Email is invalid."
            )
        try:
            normalized_email = validate_email(
                email.strip(),
                check_deliverability=False,
            ).normalized
        except EmailNotValidError as exc:
            raise CompanyProfileValidationError(
                "Email is invalid."
            ) from exc
        if len(normalized_email) > 320:
            raise CompanyProfileValidationError(
                "Email is invalid."
            )

    normalized_website = None
    if website is not None:
        if not isinstance(website, str):
            raise CompanyProfileValidationError(
                "Website is invalid."
            )
        normalized_website = website.strip()
        if len(normalized_website) > 2048:
            raise CompanyProfileValidationError(
                "Website is invalid."
            )
        try:
            _HTTP_URL.validate_python(normalized_website)
        except ValidationError as exc:
            raise CompanyProfileValidationError(
                "Website is invalid."
            ) from exc

    normalized_phone = None
    if phone is not None:
        if not isinstance(phone, str):
            raise CompanyProfileValidationError(
                "Phone is invalid."
            )
        normalized_phone = phone.strip()
        if not normalized_phone or len(normalized_phone) > 50:
            raise CompanyProfileValidationError(
                "Phone is invalid."
            )
        try:
            parsed = phonenumbers.parse(normalized_phone, None)
        except phonenumbers.NumberParseException:
            pass
        else:
            if (
                not parsed.extension
                and phonenumbers.is_valid_number(parsed)
            ):
                normalized_phone = phonenumbers.format_number(
                    parsed,
                    phonenumbers.PhoneNumberFormat.E164,
                )

    return {
        "legal_name": legal_name,
        "email": normalized_email,
        "website": normalized_website,
        "phone": normalized_phone,
    }

class TenantCompanyProfileConflictError(Exception):
    pass


class TenantCompanyProfileOperationError(Exception):
    pass


class CompanyProfileRowVersionConflictError(Exception):
    status_code = 409
    code = "ROW_VERSION_CONFLICT"


class TenantCompanyProfileNotFoundError(Exception):
    pass


class TenantCompanyProfileService:
    def __init__(
        self,
        *,
        session,
        repository,
        event_repository=None,
        file_repository=None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.file_repository = (
            file_repository
            if file_repository is not None
            else FileRepository(session)
        )
        self.event_repository = (
            event_repository
            if event_repository is not None
            else EventRepository(session)
        )

    async def get_profile(
        self,
        *,
        actor,
    ):
        from services.platform_company_profile_access import (
            CompanyProfilePermissionDeniedError,
        )

        if (
            not isinstance(actor.user_id, UUID)
            or not isinstance(actor.tenant_id, UUID)
            or actor.status != "active"
        ):
            raise CompanyProfilePermissionDeniedError(
                "Company profile access is not available."
            )
        profile = await self.repository.get_for_tenant(
            tenant_id=actor.tenant_id,
        )
        if profile is not None and profile.tenant_id != actor.tenant_id:
            raise CompanyProfilePermissionDeniedError(
                "Company profile access is not available."
            )
        return profile

    async def create_profile(
        self,
        *,
        actor,
        legal_name: str,
        email: str | None,
        website: str | None,
        phone: str | None,
        commercial_name: str | None = None,
        registered_address_raw: str | None = None,
        operational_address_raw: str | None = None,
        signatory_name: str | None = None,
        signatory_position: str | None = None,
        logo_file_id: UUID | None = None,
        construction_access_context=None,
        trace_id: str | None = None,
    ):
        require_company_profile_manage(
            actor=actor,
            construction_access_context=construction_access_context,
        )

        if (
            not isinstance(actor.tenant_id, UUID)
            or not isinstance(actor.user_id, UUID)
        ):
            raise CompanyProfileValidationError(
                "Verified organization actor is required."
            )

        normalized = normalize_company_profile_fields(
            legal_name=legal_name,
            email=email,
            website=website,
            phone=phone,
        )

        optional_values = (
            ("commercial_name", commercial_name, 255),
            ("registered_address_raw", registered_address_raw, None),
            ("operational_address_raw", operational_address_raw, None),
            ("signatory_name", signatory_name, 255),
            ("signatory_position", signatory_position, 255),
        )
        for name, value, max_length in optional_values:
            if value is not None and (
                not isinstance(value, str)
                or (
                    max_length is not None
                    and len(value) > max_length
                )
            ):
                raise CompanyProfileValidationError(
                    f"{name} is invalid."
                )

        try:
            existing = await self.repository.get_for_tenant(
                tenant_id=actor.tenant_id
            )
            if existing is not None:
                raise TenantCompanyProfileConflictError(
                    "Company profile already exists."
                )

            if logo_file_id is not None:
                if not isinstance(logo_file_id, UUID):
                    raise CompanyProfileValidationError(
                        "Company logo file ID is invalid."
                    )
                try:
                    logo = await (
                        self.file_repository
                        .get_organization_downloadable_file(
                            tenant_id=actor.tenant_id,
                            file_id=logo_file_id,
                        )
                    )
                except FileStorageObjectNotFoundError as exc:
                    raise CompanyProfileValidationError(
                        "Company logo file is not available."
                    ) from exc
                validate_company_logo_file(
                    storage_object=logo,
                    tenant_id=actor.tenant_id,
                )

            profile = await self.repository.create_profile(
                tenant_id=actor.tenant_id,
                legal_name=normalized["legal_name"],
                commercial_name=commercial_name,
                registered_address_raw=registered_address_raw,
                operational_address_raw=operational_address_raw,
                phone=normalized["phone"],
                email=normalized["email"],
                website=normalized["website"],
                signatory_name=signatory_name,
                signatory_position=signatory_position,
                logo_file_id=logo_file_id,
            )
            if profile.tenant_id != actor.tenant_id:
                raise TenantCompanyProfileOperationError(
                    "Company profile scope is invalid."
                )

            await self.event_repository.create_event(
                event_type="platform_company_profile_created",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="tenant_company_profile",
                entity_id=profile.id,
                payload={
                    "operation": "create",
                    "changes": [],
                },
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return profile
        except (
            TenantCompanyProfileConflictError,
            CompanyProfileValidationError,
        ):
            await self.session.rollback()
            raise
        except IntegrityError as exc:
            await self.session.rollback()
            if _is_tenant_profile_unique_violation(exc):
                raise TenantCompanyProfileConflictError(
                    "Company profile already exists."
                ) from exc
            raise TenantCompanyProfileOperationError(
                "Company profile operation failed."
            ) from exc
        except Exception as exc:
            await self.session.rollback()
            raise TenantCompanyProfileOperationError(
                "Company profile operation failed."
            ) from exc

    async def update_profile(
        self,
        *,
        actor,
        expected_row_version: int,
        changes: dict,
        construction_access_context=None,
        trace_id: str | None = None,
    ):
        require_company_profile_manage(
            actor=actor,
            construction_access_context=construction_access_context,
        )

        if (
            not isinstance(actor.tenant_id, UUID)
            or not isinstance(actor.user_id, UUID)
        ):
            raise CompanyProfileValidationError(
                "Verified organization actor is required."
            )
        if (
            isinstance(expected_row_version, bool)
            or not isinstance(expected_row_version, int)
            or expected_row_version <= 0
        ):
            raise CompanyProfileValidationError(
                "Expected row version is invalid."
            )
        if not isinstance(changes, dict) or not changes:
            raise CompanyProfileValidationError(
                "Profile changes are required."
            )

        allowed = {
            "legal_name",
            "commercial_name",
            "registered_address_raw",
            "operational_address_raw",
            "phone",
            "email",
            "website",
            "signatory_name",
            "signatory_position",
            "logo_file_id",
        }
        if not set(changes).issubset(allowed):
            raise CompanyProfileValidationError(
                "Profile contains unsupported fields."
            )

        try:
            profile = (
                await self.repository.get_for_tenant_for_update(
                    tenant_id=actor.tenant_id
                )
            )
            if profile is None:
                raise TenantCompanyProfileNotFoundError(
                    "Company profile is not available."
                )
            if profile.tenant_id != actor.tenant_id:
                raise TenantCompanyProfileOperationError(
                    "Company profile scope is invalid."
                )
            if profile.row_version != expected_row_version:
                raise CompanyProfileRowVersionConflictError()

            normalized_changes = {}
            for name, value in changes.items():
                if name in {
                    "legal_name", "email", "website", "phone"
                }:
                    normalized = normalize_company_profile_fields(
                        legal_name=(
                            value
                            if name == "legal_name"
                            else profile.legal_name
                        ),
                        email=value if name == "email" else None,
                        website=value if name == "website" else None,
                        phone=value if name == "phone" else None,
                    )
                    value = normalized[name]
                elif name == "logo_file_id":
                    if value is not None and not isinstance(value, UUID):
                        raise CompanyProfileValidationError(
                            "Company logo file ID is invalid."
                        )
                    if value is not None and value != profile.logo_file_id:
                        try:
                            logo = await (
                                self.file_repository
                                .get_organization_downloadable_file(
                                    tenant_id=actor.tenant_id,
                                    file_id=value,
                                )
                            )
                        except FileStorageObjectNotFoundError as exc:
                            raise CompanyProfileValidationError(
                                "Company logo file is not available."
                            ) from exc
                        validate_company_logo_file(
                            storage_object=logo,
                            tenant_id=actor.tenant_id,
                        )
                elif value is not None:
                    max_length = (
                        255
                        if name in {
                            "commercial_name",
                            "signatory_name",
                            "signatory_position",
                        }
                        else None
                    )
                    if (
                        not isinstance(value, str)
                        or (
                            max_length is not None
                            and len(value) > max_length
                        )
                    ):
                        raise CompanyProfileValidationError(
                            f"{name} is invalid."
                        )

                if getattr(profile, name) != value:
                    normalized_changes[name] = value

            if not normalized_changes:
                await self.session.commit()
                return profile

            profile = await self.repository.update_profile(
                profile=profile,
                values=normalized_changes,
            )
            await self.event_repository.create_event(
                event_type="platform_company_profile_updated",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="tenant_company_profile",
                entity_id=profile.id,
                payload={
                    "operation": "update",
                    "fields": sorted(normalized_changes),
                },
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return profile
        except (
            CompanyProfileRowVersionConflictError,
            TenantCompanyProfileNotFoundError,
            CompanyProfileValidationError,
        ):
            await self.session.rollback()
            raise
        except StaleDataError as exc:
            await self.session.rollback()
            raise CompanyProfileRowVersionConflictError() from exc
        except Exception as exc:
            await self.session.rollback()
            raise TenantCompanyProfileOperationError(
                "Company profile operation failed."
            ) from exc


def build_tenant_company_profile_service(session):
    from database.repositories.company_profiles import (
        TenantCompanyProfileRepository,
    )

    return TenantCompanyProfileService(
        session=session,
        repository=TenantCompanyProfileRepository(session),
    )
