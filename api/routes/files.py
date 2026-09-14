from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Request,
    status,
)

from api.auth import (
    get_current_actor,
    require_authenticated_user_rate_limit,
)
from api.dependencies import get_file_service
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    FileDownloadResponse,
    FileCompleteResponse,
    FileUploadRequest,
    FileUploadRequestResponse,
)
from services.api_identity import ApiActorContext
from database.repositories.files import (
    FileStorageObjectNotFoundError,
)
from services.files import (
    FileService,
    FileUploadValidationError,
    FileUploadVerificationError,
)


router = APIRouter(
    dependencies=[
        Depends(
            require_authenticated_user_rate_limit()
        ),
    ],
)


@router.post(
    "/files/upload-request",
    response_model=FileUploadRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_file_upload_request(
    payload: FileUploadRequest,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        FileService,
        Depends(get_file_service),
    ],
):
    try:
        result = await service.create_upload_request(
            tenant_id=actor.tenant_id,
            owner_user_id=actor.user_id,
            object_type=payload.object_type,
            entity_id=payload.entity_id,
            filename=payload.filename,
            mime_type=payload.mime_type,
            size_bytes=payload.size_bytes,
        )
    except FileUploadValidationError as exc:
        raise ApiHttpError(
            status_code=422,
            code="file_validation_error",
            message="File upload data is not valid.",
        ) from exc

    return success_envelope(
        data={
            "id": result.id,
            "upload_url": result.upload_url,
            "expires_in": result.expires_in,
            "status": result.status,
            "mime_type": result.mime_type,
            "size_bytes": result.size_bytes,
        },
        meta={},
        request_id=request.state.request_id,
    )



@router.post(
    "/files/{file_id}/complete",
    response_model=FileCompleteResponse,
)
async def complete_file_upload(
    file_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        FileService,
        Depends(get_file_service),
    ],
):
    try:
        result = await service.complete_upload(
            tenant_id=actor.tenant_id,
            owner_user_id=actor.user_id,
            file_id=file_id,
        )
    except FileStorageObjectNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="file_not_found",
            message="File was not found.",
        ) from exc
    except FileUploadVerificationError as exc:
        raise ApiHttpError(
            status_code=422,
            code="file_verification_failed",
            message="Uploaded file could not be verified.",
        ) from exc

    return success_envelope(
        data={
            "id": result.id,
            "status": result.status,
            "antivirus_status": (
                result.antivirus_status
            ),
            "mime_type": result.mime_type,
            "size_bytes": result.size_bytes,
            "completed_at": result.completed_at,
        },
        meta={},
        request_id=request.state.request_id,
    )



@router.get(
    "/files/{file_id}/download-url",
    response_model=FileDownloadResponse,
)
async def create_file_download_url(
    file_id: UUID,
    request: Request,
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
    service: Annotated[
        FileService,
        Depends(get_file_service),
    ],
):
    try:
        result = await service.create_download_url(
            tenant_id=actor.tenant_id,
            owner_user_id=actor.user_id,
            file_id=file_id,
        )
    except FileStorageObjectNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="file_not_found",
            message="File was not found.",
        ) from exc

    return success_envelope(
        data={
            "id": result.id,
            "download_url": result.download_url,
            "expires_in": result.expires_in,
            "mime_type": result.mime_type,
            "size_bytes": result.size_bytes,
        },
        meta={},
        request_id=request.state.request_id,
    )
