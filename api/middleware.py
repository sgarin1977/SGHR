from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import (
    BaseHTTPMiddleware,
)
from starlette.requests import Request


class PartnerApiRequestLoggingMiddleware(
    BaseHTTPMiddleware
):
    def __init__(
        self,
        app,
        *,
        logger,
    ):
        super().__init__(app)
        self.logger = logger

    async def dispatch(self, request: Request, call_next):
        started_at = perf_counter()
        response = None

        try:
            response = await call_next(request)
            return response
        finally:
            principal = getattr(
                request.state, "partner_api_principal", None,
            )
            if principal is not None:
                request_id = (
                    getattr(request.state, "request_id", None)
                    or (
                        response.headers.get("X-Request-ID")
                        if response is not None else None
                    )
                    or str(uuid4())
                )
                await self.logger.record_request(
                    principal=principal,
                    request_id=request_id,
                    endpoint=request.url.path,
                    method=request.method,
                    status_code=(
                        response.status_code if response is not None else 500
                    ),
                    duration_ms=max(
                        0, int((perf_counter() - started_at) * 1000),
                    ),
                    ip=(
                        request.client.host
                        if request.client is not None else None
                    ),
                    user_agent=request.headers.get("User-Agent"),
                )
