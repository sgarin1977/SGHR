from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from api.auth import (
    get_current_actor,
    require_authenticated_user_rate_limit,
)
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    CompanyBankAccountCreateRequest,
    CompanyBankAccountDeactivateRequest,
    CompanyBankAccountUpdateRequest,
    CompanyBankAccountListResponse,
    CompanyBankAccountResponse,
    CompanyIdentifierCreateRequest,
    CompanyIdentifierDeleteResponse,
    CompanyIdentifierListResponse,
    CompanyIdentifierResponse,
    CompanyProfileCreateRequest,
    CompanyProfileReadResponse,
    CompanyProfileResponse,
    CompanyProfileUpdateRequest,
)
from services.platform_company_bank_accounts import (
    CompanyBankAccountProfileNotFoundError,
    CompanyBankAccountNotFoundError,
    CompanyBankAccountConflictError,
    CompanyBankAccountOperationError,
    CompanyBankAccountRowVersionConflictError,
    CompanyBankAccountValidationError,
    TenantCompanyBankAccountService,
    build_tenant_company_bank_account_service,
)
from services.api_identity import ApiActorContext
from services.platform_company_identifiers import (
    CompanyIdentifierProfileNotFoundError,
    CompanyIdentifierConflictError,
    CompanyIdentifierNotFoundError,
    CompanyIdentifierOperationError,
    CompanyIdentifierValidationError,
    TenantCompanyIdentifierService,
    build_tenant_company_identifier_service,
)
from services.platform_company_profile_access import (
    CompanyProfilePermissionDeniedError,
    load_company_profile_write_context,
)
from services.platform_company_profiles import (
    CompanyProfileRowVersionConflictError,
    CompanyProfileValidationError,
    TenantCompanyProfileConflictError,
    TenantCompanyProfileNotFoundError,
    TenantCompanyProfileOperationError,
    TenantCompanyProfileService,
    build_tenant_company_profile_service,
)


router = APIRouter(
    dependencies=[
        Depends(require_authenticated_user_rate_limit()),
    ],
)


def get_company_identifier_service(
    session=Depends(get_api_session),
) -> TenantCompanyIdentifierService:
    return build_tenant_company_identifier_service(session)


def get_company_profile_service(
    session=Depends(get_api_session),
) -> TenantCompanyProfileService:
    return build_tenant_company_profile_service(session)


async def get_company_profile_write_context(
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
):
    try:
        return await load_company_profile_write_context(actor=actor)
    except CompanyProfilePermissionDeniedError as exc:
        raise ApiHttpError(
            status_code=403,
            code="forbidden",
            message="Company profile access is not available.",
        ) from exc


def profile_data(profile) -> dict:
    fields = (
        "id",
        "tenant_id",
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
        "created_at",
        "updated_at",
        "row_version",
    )
    return {field: getattr(profile, field) for field in fields}


def profile_error(exc: Exception) -> ApiHttpError:
    if isinstance(exc, CompanyProfilePermissionDeniedError):
        return ApiHttpError(
            status_code=403,
            code="forbidden",
            message="Company profile access is not available.",
        )
    if isinstance(exc, CompanyProfileValidationError):
        return ApiHttpError(
            status_code=422,
            code="validation_error",
            message="Company profile data is not valid.",
        )
    if isinstance(exc, TenantCompanyProfileConflictError):
        return ApiHttpError(
            status_code=409,
            code="company_profile_exists",
            message="Company profile already exists.",
        )
    if isinstance(exc, CompanyProfileRowVersionConflictError):
        return ApiHttpError(
            status_code=409,
            code=exc.code,
            message="Company profile has changed.",
        )
    if isinstance(exc, TenantCompanyProfileNotFoundError):
        return ApiHttpError(
            status_code=404,
            code="company_profile_not_found",
            message="Company profile is not available.",
        )
    return ApiHttpError(
        status_code=500,
        code="internal_error",
        message="Company profile operation failed.",
    )


_PROFILE_ERRORS = (
    CompanyProfilePermissionDeniedError,
    CompanyProfileValidationError,
    TenantCompanyProfileConflictError,
    CompanyProfileRowVersionConflictError,
    TenantCompanyProfileNotFoundError,
    TenantCompanyProfileOperationError,
)


@router.get(
    "/organization/company-profile",
    response_model=CompanyProfileReadResponse,
)
async def read_company_profile(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        TenantCompanyProfileService,
        Depends(get_company_profile_service),
    ],
):
    try:
        profile = await service.get_profile(actor=actor)
    except CompanyProfilePermissionDeniedError as exc:
        raise profile_error(exc) from exc

    return success_envelope(
        data=profile_data(profile) if profile is not None else None,
        request_id=request.state.request_id,
    )


@router.post(
    "/organization/company-profile",
    response_model=CompanyProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_profile(
    payload: CompanyProfileCreateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    access_context: Annotated[
        object,
        Depends(get_company_profile_write_context),
    ],
    service: Annotated[
        TenantCompanyProfileService,
        Depends(get_company_profile_service),
    ],
):
    try:
        profile = await service.create_profile(
            actor=actor,
            construction_access_context=access_context,
            trace_id=request.state.request_id,
            **payload.model_dump(),
        )
    except _PROFILE_ERRORS as exc:
        raise profile_error(exc) from exc

    return success_envelope(
        data=profile_data(profile),
        request_id=request.state.request_id,
    )


@router.patch(
    "/organization/company-profile",
    response_model=CompanyProfileResponse,
)
async def update_company_profile(
    payload: CompanyProfileUpdateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    access_context: Annotated[
        object,
        Depends(get_company_profile_write_context),
    ],
    service: Annotated[
        TenantCompanyProfileService,
        Depends(get_company_profile_service),
    ],
):
    values = payload.model_dump(exclude_unset=True)
    expected_row_version = values.pop("expected_row_version")

    try:
        profile = await service.update_profile(
            actor=actor,
            construction_access_context=access_context,
            expected_row_version=expected_row_version,
            changes=values,
            trace_id=request.state.request_id,
        )
    except _PROFILE_ERRORS as exc:
        raise profile_error(exc) from exc

    return success_envelope(
        data=profile_data(profile),
        request_id=request.state.request_id,
    )


@router.get(
    "/organization/company-profile/identifiers",
    response_model=CompanyIdentifierListResponse,
)
async def list_company_identifiers(
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        TenantCompanyIdentifierService,
        Depends(get_company_identifier_service),
    ],
):
    try:
        identifiers = await service.list_identifiers(actor=actor)
    except CompanyProfilePermissionDeniedError as exc:
        raise profile_error(exc) from exc

    fields = (
        "id",
        "country_code",
        "identifier_type",
        "identifier_value",
        "is_primary",
        "created_at",
        "updated_at",
    )
    return success_envelope(
        data=[
            {field: getattr(item, field) for field in fields}
            for item in identifiers
        ],
        request_id=request.state.request_id,
    )


@router.post(
    "/organization/company-profile/identifiers",
    response_model=CompanyIdentifierResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_identifier(
    payload: CompanyIdentifierCreateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    access_context: Annotated[
        object,
        Depends(get_company_profile_write_context),
    ],
    service: Annotated[
        TenantCompanyIdentifierService,
        Depends(get_company_identifier_service),
    ],
):
    try:
        identifier = await service.create_identifier(
            actor=actor,
            construction_access_context=access_context,
            country_code=payload.country_code,
            identifier_type=payload.identifier_type,
            identifier_value=payload.identifier_value,
            is_primary=payload.is_primary,
            trace_id=request.state.request_id,
        )
    except CompanyProfilePermissionDeniedError as exc:
        raise profile_error(exc) from exc
    except CompanyIdentifierValidationError as exc:
        raise ApiHttpError(
            status_code=422,
            code="validation_error",
            message="Company identifier data is not valid.",
        ) from exc
    except CompanyIdentifierConflictError as exc:
        raise ApiHttpError(
            status_code=409,
            code="company_identifier_conflict",
            message="Company identifier conflicts with an existing record.",
        ) from exc
    except CompanyIdentifierProfileNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="company_profile_not_found",
            message="Company profile is not available.",
        ) from exc
    except CompanyIdentifierOperationError as exc:
        raise ApiHttpError(
            status_code=500,
            code="internal_error",
            message="Company identifier operation failed.",
        ) from exc

    fields = (
        "id",
        "country_code",
        "identifier_type",
        "identifier_value",
        "is_primary",
        "created_at",
        "updated_at",
    )
    return success_envelope(
        data={field: getattr(identifier, field) for field in fields},
        request_id=request.state.request_id,
    )


@router.post(
    "/organization/company-profile/identifiers/{identifier_id}/replace",
    response_model=CompanyIdentifierResponse,
)
async def replace_company_identifier(
    identifier_id: UUID,
    payload: CompanyIdentifierCreateRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    access_context: Annotated[
        object,
        Depends(get_company_profile_write_context),
    ],
    service: Annotated[
        TenantCompanyIdentifierService,
        Depends(get_company_identifier_service),
    ],
):
    try:
        identifier = await service.replace_identifier(
            actor=actor,
            identifier_id=identifier_id,
            construction_access_context=access_context,
            country_code=payload.country_code,
            identifier_type=payload.identifier_type,
            identifier_value=payload.identifier_value,
            is_primary=payload.is_primary,
            trace_id=request.state.request_id,
        )
    except CompanyProfilePermissionDeniedError as exc:
        raise profile_error(exc) from exc
    except CompanyIdentifierValidationError as exc:
        raise ApiHttpError(
            status_code=422,
            code="validation_error",
            message="Company identifier data is not valid.",
        ) from exc
    except CompanyIdentifierConflictError as exc:
        raise ApiHttpError(
            status_code=409,
            code="company_identifier_conflict",
            message="Company identifier conflicts with an existing record.",
        ) from exc
    except CompanyIdentifierNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="company_identifier_not_found",
            message="Company identifier is not available.",
        ) from exc
    except CompanyIdentifierProfileNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="company_profile_not_found",
            message="Company profile is not available.",
        ) from exc
    except CompanyIdentifierOperationError as exc:
        raise ApiHttpError(
            status_code=500,
            code="internal_error",
            message="Company identifier operation failed.",
        ) from exc

    fields = (
        "id",
        "country_code",
        "identifier_type",
        "identifier_value",
        "is_primary",
        "created_at",
        "updated_at",
    )
    return success_envelope(
        data={field: getattr(identifier, field) for field in fields},
        request_id=request.state.request_id,
    )


@router.delete(
    "/organization/company-profile/identifiers/{identifier_id}",
    response_model=CompanyIdentifierDeleteResponse,
)
async def delete_company_identifier(
    identifier_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    access_context: Annotated[
        object,
        Depends(get_company_profile_write_context),
    ],
    service: Annotated[
        TenantCompanyIdentifierService,
        Depends(get_company_identifier_service),
    ],
):
    try:
        await service.delete_identifier(
            actor=actor,
            identifier_id=identifier_id,
            construction_access_context=access_context,
            trace_id=request.state.request_id,
        )
    except CompanyProfilePermissionDeniedError as exc:
        raise profile_error(exc) from exc
    except CompanyIdentifierValidationError as exc:
        raise ApiHttpError(
            status_code=422,
            code="validation_error",
            message="Company identifier data is not valid.",
        ) from exc
    except CompanyIdentifierNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="company_identifier_not_found",
            message="Company identifier is not available.",
        ) from exc
    except CompanyIdentifierProfileNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="company_profile_not_found",
            message="Company profile is not available.",
        ) from exc
    except CompanyIdentifierOperationError as exc:
        raise ApiHttpError(
            status_code=500,
            code="internal_error",
            message="Company identifier operation failed.",
        ) from exc

    return success_envelope(
        data={"id": identifier_id, "deleted": True},
        request_id=request.state.request_id,
    )


def get_company_bank_account_service(
    session=Depends(get_api_session),
) -> TenantCompanyBankAccountService:
    return build_tenant_company_bank_account_service(session)


_BANK_ACCOUNT_FIELDS = (
    "id", "label", "account_holder_name", "bank_name",
    "country_code", "iban", "bic_swift", "account_number",
    "currency", "is_primary", "is_active", "show_in_documents",
    "created_at", "updated_at", "row_version",
)


def bank_account_data(account) -> dict:
    return {
        field: getattr(account, field)
        for field in _BANK_ACCOUNT_FIELDS
    }


def bank_account_error(exc: Exception) -> ApiHttpError:
    if isinstance(exc, CompanyProfilePermissionDeniedError):
        return profile_error(exc)
    if isinstance(exc, CompanyBankAccountValidationError):
        return ApiHttpError(
            status_code=422,
            code="validation_error",
            message="Company bank account data is not valid.",
        )
    if isinstance(exc, CompanyBankAccountConflictError):
        return ApiHttpError(
            status_code=409,
            code="company_bank_account_conflict",
            message="Primary bank account already exists.",
        )
    if isinstance(exc, CompanyBankAccountProfileNotFoundError):
        return ApiHttpError(
            status_code=404,
            code="company_profile_not_found",
            message="Company profile is not available.",
        )
    if isinstance(exc, CompanyBankAccountNotFoundError):
        return ApiHttpError(
            status_code=404,
            code="company_bank_account_not_found",
            message="Company bank account is not available.",
        )
    if isinstance(exc, CompanyBankAccountRowVersionConflictError):
        return ApiHttpError(
            status_code=409,
            code="ROW_VERSION_CONFLICT",
            message="Company bank account has changed.",
        )
    return ApiHttpError(
        status_code=500,
        code="internal_error",
        message="Company bank account operation failed.",
    )


_BANK_ACCOUNT_ERRORS = (
    CompanyProfilePermissionDeniedError,
    CompanyBankAccountValidationError,
    CompanyBankAccountRowVersionConflictError,
    CompanyBankAccountOperationError,
)


@router.get(
    "/organization/company-profile/bank-accounts",
    response_model=CompanyBankAccountListResponse,
)
async def list_company_bank_accounts(
    request: Request,
    actor: Annotated[ApiActorContext, Depends(get_current_actor)],
    service: Annotated[
        TenantCompanyBankAccountService,
        Depends(get_company_bank_account_service),
    ],
):
    try:
        accounts = await service.list_accounts(actor=actor)
    except _BANK_ACCOUNT_ERRORS as exc:
        raise bank_account_error(exc) from exc
    return success_envelope(
        data=[bank_account_data(item) for item in accounts],
        request_id=request.state.request_id,
    )


@router.post(
    "/organization/company-profile/bank-accounts",
    response_model=CompanyBankAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_company_bank_account(
    payload: CompanyBankAccountCreateRequest,
    request: Request,
    actor: Annotated[ApiActorContext, Depends(get_current_actor)],
    access_context: Annotated[
        object, Depends(get_company_profile_write_context),
    ],
    service: Annotated[
        TenantCompanyBankAccountService,
        Depends(get_company_bank_account_service),
    ],
):
    try:
        account = await service.create_account(
            actor=actor,
            construction_access_context=access_context,
            trace_id=request.state.request_id,
            **payload.model_dump(),
        )
    except _BANK_ACCOUNT_ERRORS as exc:
        raise bank_account_error(exc) from exc
    return success_envelope(
        data=bank_account_data(account),
        request_id=request.state.request_id,
    )


@router.patch(
    "/organization/company-profile/bank-accounts/{account_id}",
    response_model=CompanyBankAccountResponse,
)
async def update_company_bank_account(
    account_id: UUID,
    payload: CompanyBankAccountUpdateRequest,
    request: Request,
    actor: Annotated[ApiActorContext, Depends(get_current_actor)],
    access_context: Annotated[
        object, Depends(get_company_profile_write_context),
    ],
    service: Annotated[
        TenantCompanyBankAccountService,
        Depends(get_company_bank_account_service),
    ],
):
    values = payload.model_dump(exclude_unset=True)
    expected_row_version = values.pop("expected_row_version")
    try:
        account = await service.update_account(
            actor=actor,
            account_id=account_id,
            expected_row_version=expected_row_version,
            changes=values,
            construction_access_context=access_context,
            trace_id=request.state.request_id,
        )
    except _BANK_ACCOUNT_ERRORS as exc:
        raise bank_account_error(exc) from exc
    return success_envelope(
        data=bank_account_data(account),
        request_id=request.state.request_id,
    )


@router.post(
    "/organization/company-profile/bank-accounts/{account_id}/deactivate",
    response_model=CompanyBankAccountResponse,
)
async def deactivate_company_bank_account(
    account_id: UUID,
    payload: CompanyBankAccountDeactivateRequest,
    request: Request,
    actor: Annotated[ApiActorContext, Depends(get_current_actor)],
    access_context: Annotated[
        object, Depends(get_company_profile_write_context),
    ],
    service: Annotated[
        TenantCompanyBankAccountService,
        Depends(get_company_bank_account_service),
    ],
):
    try:
        account = await service.deactivate_account(
            actor=actor,
            account_id=account_id,
            expected_row_version=payload.expected_row_version,
            construction_access_context=access_context,
            trace_id=request.state.request_id,
        )
    except _BANK_ACCOUNT_ERRORS as exc:
        raise bank_account_error(exc) from exc
    return success_envelope(
        data=bank_account_data(account),
        request_id=request.state.request_id,
    )
