from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

import pycountry
from stdnum import bic as bic_validator, iban as iban_validator
from stdnum.exceptions import ValidationError

from services.platform_company_profile_access import (
    CompanyProfilePermissionDeniedError,
    require_company_profile_manage,
)


class BankAccountNotDocumentVisibleError(ValueError):
    pass


DOCUMENT_BANK_FIELDS = (
    "account_holder_name",
    "bank_name",
    "iban",
    "account_number",
    "bic_swift",
    "currency",
)


def project_document_bank_account(*, account):
    if (
        account is None
        or getattr(account, "is_active", None) is not True
        or getattr(account, "show_in_documents", None) is not True
    ):
        raise BankAccountNotDocumentVisibleError(
            "Bank account is not available for documents."
        )

    return {
        field: getattr(account, field)
        for field in DOCUMENT_BANK_FIELDS
    }


class CompanyBankAccountValidationError(ValueError):
    pass


def _optional_bank_text(value, *, max_length=None):
    if value is None:
        return None
    if not isinstance(value, str):
        raise CompanyBankAccountValidationError(
            "Bank account field is invalid."
        )
    normalized = value.strip()
    if max_length is not None and len(normalized) > max_length:
        raise CompanyBankAccountValidationError(
            "Bank account field is too long."
        )
    return normalized or None


def normalize_company_bank_account_fields(
    *, account_holder_name, country_code, iban,
    bic_swift, account_number, currency,
):
    holder = _optional_bank_text(
        account_holder_name, max_length=255
    )
    number = _optional_bank_text(
        account_number, max_length=100
    )
    bank_country = _optional_bank_text(
        country_code, max_length=2
    )
    if bank_country is not None:
        bank_country = bank_country.upper()
        if pycountry.countries.get(alpha_2=bank_country) is None:
            raise CompanyBankAccountValidationError(
                "Bank country code is invalid."
            )
    currency_code = _optional_bank_text(
        currency, max_length=3
    )
    if currency_code is not None:
        currency_code = currency_code.upper()
    if (
        currency_code is None
        or pycountry.currencies.get(alpha_3=currency_code) is None
    ):
        raise CompanyBankAccountValidationError(
            "Currency code is invalid."
        )

    raw_iban = _optional_bank_text(iban)
    raw_bic = _optional_bank_text(bic_swift)
    try:
        normalized_iban = (
            iban_validator.validate(raw_iban, check_country=False)
            if raw_iban is not None else None
        )
        normalized_bic = (
            bic_validator.validate(raw_bic)
            if raw_bic is not None else None
        )
    except ValidationError as exc:
        raise CompanyBankAccountValidationError(
            "Bank code is invalid."
        ) from exc
    if holder is None or (normalized_iban is None and number is None):
        raise CompanyBankAccountValidationError(
            "Bank account details are incomplete."
        )
    return {
        "account_holder_name": holder,
        "country_code": bank_country,
        "iban": normalized_iban,
        "bic_swift": normalized_bic,
        "account_number": number,
        "currency": currency_code,
    }

class CompanyBankAccountOperationError(Exception):
    pass


def _is_primary_account_unique_conflict(error: IntegrityError) -> bool:
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
            getattr(current, "constraint_name", None)
            == "uq_tenant_company_bank_accounts_active_primary_currency"
        )
        pending.append(getattr(current, "__cause__", None))
        pending.append(getattr(current, "__context__", None))

    return has_unique_sqlstate and has_confirmed_index


class CompanyBankAccountConflictError(CompanyBankAccountOperationError):
    status_code = 409
    code = "company_bank_account_conflict"


class CompanyBankAccountProfileNotFoundError(
    CompanyBankAccountOperationError
):
    status_code = 404
    code = "company_profile_not_found"


class CompanyBankAccountNotFoundError(
    CompanyBankAccountOperationError
):
    status_code = 404
    code = "company_bank_account_not_found"


class CompanyBankAccountRowVersionConflictError(Exception):
    status_code = 409
    code = "ROW_VERSION_CONFLICT"


class TenantCompanyBankAccountService:
    def __init__(
        self, *, session, profile_repository,
        account_repository, event_repository,
    ):
        self.session = session
        self.profile_repository = profile_repository
        self.account_repository = account_repository
        self.event_repository = event_repository

    async def list_accounts(self, *, actor):
        if (
            actor is None
            or not isinstance(getattr(actor, "tenant_id", None), UUID)
            or not isinstance(getattr(actor, "user_id", None), UUID)
            or getattr(actor, "status", None) != "active"
        ):
            raise CompanyProfilePermissionDeniedError(
                "Company bank account access is not available."
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
                "Company bank account access is not available."
            )

        accounts = await self.account_repository.list_for_tenant(
            tenant_id=actor.tenant_id,
        )
        if any(
            account.tenant_company_profile_id != profile.id
            for account in accounts
        ):
            raise CompanyProfilePermissionDeniedError(
                "Company bank account access is not available."
            )
        return list(accounts)

    async def create_account(
        self, *, actor, account_holder_name, currency,
        iban=None, account_number=None, bic_swift=None,
        country_code=None, label=None, bank_name=None,
        is_primary=False, show_in_documents=False,
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
            or not isinstance(show_in_documents, bool)
        ):
            raise CompanyBankAccountValidationError(
                "Bank account context is invalid."
            )

        fields = normalize_company_bank_account_fields(
            account_holder_name=account_holder_name,
            country_code=country_code,
            iban=iban,
            bic_swift=bic_swift,
            account_number=account_number,
            currency=currency,
        )
        normalized_label = _optional_bank_text(
            label, max_length=100
        )
        normalized_bank_name = _optional_bank_text(
            bank_name, max_length=255
        )

        try:
            profile = (
                await self.profile_repository
                .get_for_tenant_for_update(
                    tenant_id=actor.tenant_id,
                )
            )
            if (
                profile is None
                or profile.tenant_id != actor.tenant_id
                or not isinstance(profile.id, UUID)
            ):
                raise CompanyBankAccountProfileNotFoundError(
                    "Company profile is not available."
                )

            account = await self.account_repository.create_account(
                profile=profile,
                label=normalized_label,
                bank_name=normalized_bank_name,
                is_primary=is_primary,
                show_in_documents=show_in_documents,
                **fields,
            )
            if account.tenant_company_profile_id != profile.id:
                raise CompanyBankAccountOperationError(
                    "Bank account scope is invalid."
                )

            await self.event_repository.create_event(
                event_type="platform_company_bank_account_created",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="tenant_company_bank_account",
                entity_id=account.id,
                payload={"operation": "create"},
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return account
        except Exception as exc:
            await self.session.rollback()
            if isinstance(exc, CompanyBankAccountOperationError):
                raise
            if (
                isinstance(exc, IntegrityError)
                and _is_primary_account_unique_conflict(exc)
            ):
                raise CompanyBankAccountConflictError(
                    "Primary bank account already exists."
                ) from exc
            raise CompanyBankAccountOperationError(
                "Bank account operation failed."
            ) from exc

    async def update_account(
        self, *, actor, account_id, expected_row_version,
        changes, construction_access_context=None, trace_id=None,
    ):
        require_company_profile_manage(
            actor=actor,
            construction_access_context=construction_access_context,
        )
        allowed = {
            "label", "account_holder_name", "bank_name",
            "country_code", "iban", "bic_swift",
            "account_number", "currency", "is_primary",
            "show_in_documents",
        }
        if (
            not isinstance(actor.tenant_id, UUID)
            or not isinstance(actor.user_id, UUID)
            or not isinstance(account_id, UUID)
            or isinstance(expected_row_version, bool)
            or not isinstance(expected_row_version, int)
            or expected_row_version <= 0
            or not isinstance(changes, dict)
            or not changes
            or not set(changes).issubset(allowed)
        ):
            raise CompanyBankAccountValidationError(
                "Bank account update is invalid."
            )

        try:
            profile = (
                await self.profile_repository
                .get_for_tenant_for_update(
                    tenant_id=actor.tenant_id,
                )
            )
            if (
                profile is None
                or profile.tenant_id != actor.tenant_id
            ):
                raise CompanyBankAccountProfileNotFoundError(
                    "Company profile is not available."
                )

            account = (
                await self.account_repository
                .get_for_tenant_for_update(
                    tenant_id=actor.tenant_id,
                    account_id=account_id,
                )
            )
            if (
                account is None
                or account.id != account_id
                or account.tenant_company_profile_id != profile.id
            ):
                raise CompanyBankAccountNotFoundError(
                    "Bank account is not available."
                )
            if account.row_version != expected_row_version:
                raise CompanyBankAccountRowVersionConflictError()

            fields = (
                "account_holder_name", "country_code", "iban",
                "bic_swift", "account_number", "currency",
            )
            candidate = {
                field: (
                    changes[field]
                    if field in changes
                    else getattr(account, field)
                )
                for field in fields
            }
            values = normalize_company_bank_account_fields(
                **candidate
            )
            for field, limit in (
                ("label", 100),
                ("bank_name", 255),
            ):
                value = (
                    changes[field]
                    if field in changes
                    else getattr(account, field)
                )
                values[field] = _optional_bank_text(
                    value, max_length=limit
                )
            for field in ("is_primary", "show_in_documents"):
                value = (
                    changes[field]
                    if field in changes
                    else getattr(account, field)
                )
                if not isinstance(value, bool):
                    raise CompanyBankAccountValidationError(
                        "Bank account flag is invalid."
                    )
                values[field] = value

            changed = {
                field: value
                for field, value in values.items()
                if getattr(account, field) != value
            }
            if not changed:
                await self.session.commit()
                return account

            updated = await self.account_repository.update_account(
                account=account,
                values=changed,
            )
            if (
                updated.id != account_id
                or updated.tenant_company_profile_id != profile.id
            ):
                raise CompanyBankAccountOperationError(
                    "Bank account scope is invalid."
                )
            await self.event_repository.create_event(
                event_type="platform_company_bank_account_updated",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="tenant_company_bank_account",
                entity_id=account_id,
                payload={"operation": "update"},
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return updated
        except (
            CompanyBankAccountRowVersionConflictError,
            CompanyBankAccountValidationError,
            CompanyBankAccountOperationError,
        ):
            await self.session.rollback()
            raise
        except StaleDataError as exc:
            await self.session.rollback()
            raise CompanyBankAccountRowVersionConflictError() from exc
        except Exception as exc:
            await self.session.rollback()
            if (
                isinstance(exc, IntegrityError)
                and _is_primary_account_unique_conflict(exc)
            ):
                raise CompanyBankAccountConflictError(
                    "Primary bank account already exists."
                ) from exc
            raise CompanyBankAccountOperationError(
                "Bank account operation failed."
            ) from exc

    async def deactivate_account(
        self, *, actor, account_id, expected_row_version,
        construction_access_context=None, trace_id=None,
    ):
        require_company_profile_manage(
            actor=actor,
            construction_access_context=construction_access_context,
        )
        if (
            not isinstance(actor.tenant_id, UUID)
            or not isinstance(actor.user_id, UUID)
            or not isinstance(account_id, UUID)
            or isinstance(expected_row_version, bool)
            or not isinstance(expected_row_version, int)
            or expected_row_version <= 0
        ):
            raise CompanyBankAccountValidationError(
                "Bank account deactivation is invalid."
            )

        try:
            profile = (
                await self.profile_repository
                .get_for_tenant_for_update(
                    tenant_id=actor.tenant_id,
                )
            )
            if (
                profile is None
                or profile.tenant_id != actor.tenant_id
            ):
                raise CompanyBankAccountProfileNotFoundError(
                    "Company profile is not available."
                )

            account = (
                await self.account_repository
                .get_for_tenant_for_update(
                    tenant_id=actor.tenant_id,
                    account_id=account_id,
                )
            )
            if (
                account is None
                or account.id != account_id
                or account.tenant_company_profile_id != profile.id
            ):
                raise CompanyBankAccountNotFoundError(
                    "Bank account is not available."
                )
            if account.row_version != expected_row_version:
                raise CompanyBankAccountRowVersionConflictError()

            if account.is_active is False:
                await self.session.commit()
                return account

            updated = (
                await self.account_repository.deactivate_account(
                    account=account,
                )
            )
            if (
                updated.id != account_id
                or updated.tenant_company_profile_id != profile.id
                or updated.is_active is not False
            ):
                raise CompanyBankAccountOperationError(
                    "Bank account scope is invalid."
                )

            await self.event_repository.create_event(
                event_type="platform_company_bank_account_deactivated",
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                entity_type="tenant_company_bank_account",
                entity_id=account_id,
                payload={"operation": "deactivate"},
                platform="api",
                trace_id=trace_id,
            )
            await self.session.commit()
            return updated
        except (
            CompanyBankAccountRowVersionConflictError,
            CompanyBankAccountValidationError,
            CompanyBankAccountOperationError,
        ):
            await self.session.rollback()
            raise
        except StaleDataError as exc:
            await self.session.rollback()
            raise CompanyBankAccountRowVersionConflictError() from exc
        except Exception as exc:
            await self.session.rollback()
            raise CompanyBankAccountOperationError(
                "Bank account operation failed."
            ) from exc


def build_tenant_company_bank_account_service(session):
    from database.repositories.company_profiles import (
        TenantCompanyBankAccountRepository,
        TenantCompanyProfileRepository,
    )
    from database.repositories.event import EventRepository

    return TenantCompanyBankAccountService(
        session=session,
        profile_repository=TenantCompanyProfileRepository(session),
        account_repository=TenantCompanyBankAccountRepository(session),
        event_repository=EventRepository(session),
    )
