"""Internal context supplied by the trusted Bot API dispatcher.

HTTP headers, query parameters and JSON bodies cannot supply this proof.
Construction business routes continue to require normal Platform Bearer auth.
"""
from dataclasses import dataclass, field

from fastapi import Request

from api.errors import ApiHttpError
from api.telegram_auth import TelegramAuthenticationError
from services.platform_bot_auth_proof import PlatformBotUpdateVerifier

SCOPE_KEY = "sghr.platform_bot_context"


@dataclass(frozen=True)
class PlatformBotApiContext:
    verifier: PlatformBotUpdateVerifier = field(repr=False)
    proof: object = field(repr=False)


def validate_platform_bot_context(context):
    if (
        type(context) is not PlatformBotApiContext
        or type(context.verifier) is not PlatformBotUpdateVerifier
    ):
        raise TelegramAuthenticationError(
            "Trusted Platform bot context is unavailable."
        )
    context.verifier.verify(context.proof)
    return context


def require_platform_bot_context(request: Request):
    try:
        return validate_platform_bot_context(
            request.scope.get(SCOPE_KEY)
        )
    except TelegramAuthenticationError:
        raise ApiHttpError(
            status_code=401,
            code="authentication_required",
            message="Trusted Platform bot context is required.",
        ) from None


def make_platform_bot_api_context(*, bot, update):
    verifier = PlatformBotUpdateVerifier(bot=bot, update=update)
    return PlatformBotApiContext(
        verifier=verifier,
        proof=verifier.proof,
    )


def bind_platform_bot_api_context(application, *, context):
    """Bind a server-owned proof to internal Platform bootstrap requests only."""
    validate_platform_bot_context(context)

    async def dispatch(scope, receive, send):
        if (
            scope.get("type") == "http"
            and scope.get("path", "").startswith(
                "/api/v1/platform/bot/"
            )
        ):
            scope = dict(scope)
            scope[SCOPE_KEY] = context
        await application(scope, receive, send)

    return dispatch
