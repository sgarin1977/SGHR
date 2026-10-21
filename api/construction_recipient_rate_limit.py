"""Invitation recipient guard using existing shared Platform-user limits.

No invitation token, caller tenant, target membership or Construction grant is
required for the counter. Existing recipient authentication remains unchanged.
"""
from uuid import UUID
from fastapi import Depends
from api.auth import get_current_actor
from api.dependencies import get_api_request_rate_limit_service
from api.errors import ApiHttpError
from services.api_rate_limits import ApiRequestRateLimitExceededError


async def require_invitation_recipient_rate_limit(actor=Depends(get_current_actor),
    rate=Depends(get_api_request_rate_limit_service)):
    if getattr(actor, 'status', None) != 'active' or not isinstance(getattr(actor, 'user_id', None), UUID):
        raise ApiHttpError(status_code=403, code='forbidden', message='Invitation is not available.')
    try:
        await rate.ensure_platform_user_allowed(user_id=actor.user_id)
    except ApiRequestRateLimitExceededError:
        raise ApiHttpError(status_code=429, code='RATE_LIMIT_EXCEEDED', message='Rate limit exceeded.') from None
    return actor
