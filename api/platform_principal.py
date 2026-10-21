"""Optional shared dependency for approved identity-only recipient operations.

Uses the existing Platform access token verifier and canonical users. No new
credential, token issuer, session model or public endpoint is defined. Ordinary
get_current_actor and its tenant membership checks remain the standard for
tenant APIs. This dependency alone never authorizes a client read or mutation.
"""
from typing import Annotated

from fastapi import Depends

from api.auth import get_access_token_codec, invalid_token_error, require_bearer_token
from api.dependencies import get_api_session
from api.errors import ApiHttpError
from api.security import AccessTokenClaims, AccessTokenCodec, ApiAuthenticationError
from services.api_identity import ApiIdentityAccessError
from services.api_platform_principal import (
    ApiPlatformPrincipal, ApiPlatformPrincipalOperationError, build_api_platform_principal_service,
)


def get_platform_principal_service(session=Depends(get_api_session)):
    return build_api_platform_principal_service(session=session)


async def get_authenticated_platform_principal(
    token: Annotated[str, Depends(require_bearer_token)],
    codec: Annotated[AccessTokenCodec, Depends(get_access_token_codec)],
    service=Depends(get_platform_principal_service),
) -> ApiPlatformPrincipal:
    try:
        claims = codec.decode_access_token(token)
        if not isinstance(claims, AccessTokenClaims):
            raise ApiAuthenticationError('Invalid token claims.')
        # The token's tenant claim is not used as recipient-domain authority.
        principal = await service.require_principal(user_id=claims.user_id)
        if not isinstance(principal, ApiPlatformPrincipal) or principal.user_id != claims.user_id:
            raise ApiIdentityAccessError('Platform identity is not available.')
        return principal
    except (ApiAuthenticationError, ApiIdentityAccessError):
        raise invalid_token_error() from None
    except ApiPlatformPrincipalOperationError:
        raise ApiHttpError(status_code=503, code='service_unavailable', message='Platform identity is not available.') from None
