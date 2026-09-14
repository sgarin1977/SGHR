from fastapi import APIRouter, Request

from api.responses import success_envelope
from api.schemas import HealthResponse


router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
)
async def get_api_health(
    request: Request,
):
    return success_envelope(
        data={
            "status": "ok",
            "service": "sghr-api",
            "api_version": "v1",
        },
        request_id=request.state.request_id,
    )
