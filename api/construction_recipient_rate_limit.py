"""Invitation recipient identity and limits without tenant membership."""
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import Depends

from api.auth import invalid_token_error
from api.dependencies import get_api_request_rate_limit_service
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from services.api_platform_principal import ApiPlatformPrincipal
from services.api_rate_limits import ApiRequestRateLimitExceededError


@dataclass(frozen=True)
class InvitationRecipientActor:
    user_id: UUID
    status: str = field(default="active", init=False)


def get_invitation_recipient_actor(
    principal=Depends(get_authenticated_platform_principal),
):
    if not isinstance(principal, ApiPlatformPrincipal):
        raise invalid_token_error()
    return InvitationRecipientActor(principal.user_id)


async def require_invitation_recipient_rate_limit(
    actor=Depends(get_invitation_recipient_actor),
    rate=Depends(get_api_request_rate_limit_service),
):
    if getattr(actor, "status", None) != "active" or not isinstance(
        getattr(actor, "user_id", None), UUID
    ):
        raise ApiHttpError(
            status_code=403, code="forbidden",
            message="Invitation is not available.",
        )
    try:
        await rate.ensure_platform_user_allowed(user_id=actor.user_id)
    except ApiRequestRateLimitExceededError:
        raise ApiHttpError(
            status_code=429, code="RATE_LIMIT_EXCEEDED",
            message="Rate limit exceeded.",
        ) from None
    return actor

# invitation_identity_only_v1
