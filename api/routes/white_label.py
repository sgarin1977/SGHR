from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Request,
)

from api.dependencies import (
    get_white_label_service,
)
from api.errors import ApiHttpError
from api.responses import success_envelope
from api.schemas import (
    WhiteLabelConfigResponse,
)
from services.white_label import (
    WhiteLabelService,
    WhiteLabelTenantNotFoundError,
)


router = APIRouter()


@router.get(
    "/white-label/config",
    response_model=WhiteLabelConfigResponse,
)
async def get_white_label_config(
    request: Request,
    service: Annotated[
        WhiteLabelService,
        Depends(get_white_label_service),
    ],
):
    hostname = (
        request.url.hostname
        or ""
    ).strip().lower().rstrip(".")

    if not hostname:
        raise ApiHttpError(
            status_code=404,
            code="white_label_not_found",
            message=(
                "White Label configuration "
                "is not available."
            ),
        )

    try:
        config = await service.get_config(
            hostname=hostname,
        )
    except WhiteLabelTenantNotFoundError as exc:
        raise ApiHttpError(
            status_code=404,
            code="white_label_not_found",
            message=(
                "White Label configuration "
                "is not available."
            ),
        ) from exc

    return success_envelope(
        data={
            "tenant_slug": config.tenant_slug,
            "tenant_name": config.tenant_name,
            "default_language": (
                config.default_language
            ),
            "default_currency": (
                config.default_currency
            ),
            "branding": {
                "logo_url": (
                    config.branding.logo_url
                ),
                "favicon_url": (
                    config.branding.favicon_url
                ),
                "primary_color": (
                    config.branding.primary_color
                ),
                "secondary_color": (
                    config.branding
                    .secondary_color
                ),
                "accent_color": (
                    config.branding.accent_color
                ),
                "theme_config": (
                    config.branding.theme_config
                ),
            },
            "languages": [
                {
                    "code": language.code,
                    "name": language.name,
                    "native_name": (
                        language.native_name
                    ),
                }
                for language
                in config.languages
            ],
            "suites": list(config.suites),
            "modules": list(config.modules),
            "legal_documents": [
                {
                    "doc_type": (
                        document.doc_type
                    ),
                    "version": document.version,
                    "language": (
                        document.language
                    ),
                    "title": document.title,
                    "content_url": (
                        document.content_url
                    ),
                }
                for document
                in config.legal_documents
            ],
            "pricing": [
                {
                    "code": item.code,
                    "name": item.name,
                    "description": (
                        item.description
                    ),
                    "price": item.price,
                    "currency": item.currency,
                }
                for item in config.pricing
            ],
        },
        meta={},
        request_id=request.state.request_id,
    )
