from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, Request, Security
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from api.dependencies import (
    get_api_session,
    get_tenant_module_access_service,
    get_tenant_suite_access_service,
)
from api.dependencies import (
    get_partner_api_authentication_service,
)
from api.errors import ApiHttpError
from api.security import (
    AccessTokenCodec,
    ApiAuthenticationError,
)
from api.settings import ApiAuthSettings
from services.api_identity import (
    ApiActorContext,
    ApiIdentityAccessError,
    ApiIdentityService,
)
from services.api_admin_access import (
    ADMIN_API_ROLES,
)
from services.module_access import (
    ModuleDisabledError,
    SuiteDisabledError,
    TenantModuleAccessService,
    TenantSuiteAccessService,
)


from services.partner_api_auth import (
    PartnerApiAuthenticationError,
    PartnerApiAuthenticationService,
    PartnerApiPrincipal,
)
from services.webhooks import (
    WebhookAccessContext,
)
from services.partner_api import (
    PartnerApiScopeError,
    validate_api_key_scopes,
)

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
)


async def get_partner_api_principal(
    request: Request,
    service: Annotated[
        PartnerApiAuthenticationService,
        Depends(
            get_partner_api_authentication_service
        ),
    ],
    authorization: Annotated[
        str | None,
        Header(alias="Authorization"),
    ] = None,
) -> PartnerApiPrincipal:
    api_key = parse_api_key_authorization(
        authorization
    )

    remote_ip = (
        request.client.host
        if request.client is not None
        else None
    )

    try:
        principal = await service.authenticate(
            api_key=api_key,
            remote_ip=remote_ip,
            now=datetime.now(UTC),
        )
    except PartnerApiAuthenticationError:
        raise ApiHttpError(
            status_code=401,
            code="invalid_api_key",
            message=(
                "API Key authentication failed."
            ),
            headers={
                "WWW-Authenticate": "ApiKey",
            },
        ) from None

    request.state.partner_api_principal = (
        principal
    )
    return principal


async def require_bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ],
) -> str:
    if (
        credentials is None
        or credentials.scheme.lower()
        != "bearer"
        or not credentials.credentials.strip()
    ):
        raise ApiHttpError(
            status_code=401,
            code="authentication_required",
            message="Authentication required.",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    return credentials.credentials.strip()


@lru_cache(maxsize=1)
def get_access_token_codec(
) -> AccessTokenCodec:
    settings = ApiAuthSettings.from_env()

    return AccessTokenCodec(
        secret=settings.jwt_secret,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        access_ttl_seconds=(
            settings.access_token_ttl_seconds
        ),
    )


def get_api_identity_service(
    session=Depends(get_api_session),
) -> ApiIdentityService:
    return ApiIdentityService(session)


def invalid_token_error() -> ApiHttpError:
    return ApiHttpError(
        status_code=401,
        code="invalid_token",
        message=(
            "Invalid or expired access token."
        ),
        headers={
            "WWW-Authenticate": (
                'Bearer error="invalid_token"'
            ),
        },
    )


def parse_api_key_authorization(
    authorization: str | None,
) -> str:
    if (
        not isinstance(authorization, str)
        or not authorization
        or len(authorization) > 1024
    ):
        raise ApiHttpError(
            status_code=401,
            code="invalid_api_key",
            message="API Key authentication failed.",
        )

    prefix = "ApiKey "
    if not authorization.startswith(prefix):
        raise ApiHttpError(
            status_code=401,
            code="invalid_api_key",
            message="API Key authentication failed.",
        )

    api_key = authorization[len(prefix):]

    if (
        not api_key
        or any(
            character.isspace()
            for character in api_key
        )
        or authorization
        != f"{prefix}{api_key}"
    ):
        raise ApiHttpError(
            status_code=401,
            code="invalid_api_key",
            message="API Key authentication failed.",
        )

    return api_key


async def get_current_actor(
    token: Annotated[
        str,
        Depends(require_bearer_token),
    ],
    codec: Annotated[
        AccessTokenCodec,
        Depends(get_access_token_codec),
    ],
    identity_service: Annotated[
        ApiIdentityService,
        Depends(get_api_identity_service),
    ],
) -> ApiActorContext:
    try:
        claims = codec.decode_access_token(
            token
        )
    except ApiAuthenticationError:
        raise invalid_token_error() from None

    try:
        return await (
            identity_service.require_actor(
                user_id=claims.user_id,
                tenant_id=claims.tenant_id,
            )
        )
    except ApiIdentityAccessError:
        raise invalid_token_error() from None


async def get_webhook_access_context(
    request: Request,
    codec: Annotated[
        AccessTokenCodec,
        Depends(get_access_token_codec),
    ],
    identity_service: Annotated[
        ApiIdentityService,
        Depends(get_api_identity_service),
    ],
    partner_service: Annotated[
        PartnerApiAuthenticationService,
        Depends(
            get_partner_api_authentication_service
        ),
    ],
    authorization: Annotated[
        str | None,
        Header(alias="Authorization"),
    ] = None,
) -> WebhookAccessContext:
    if (
        isinstance(authorization, str)
        and authorization.startswith(
            "ApiKey "
        )
    ):
        principal = await (
            get_partner_api_principal(
                request=request,
                service=partner_service,
                authorization=authorization,
            )
        )

        context = WebhookAccessContext(
            tenant_id=principal.tenant_id,
            principal_type="api_key",
            principal_id=principal.api_key_id,
            api_client_id=(
                principal.api_client_id
            ),
            grants=principal.scopes,
        )
    else:
        credentials = None

        if isinstance(authorization, str):
            parts = authorization.split(
                " ",
                1,
            )
            if len(parts) == 2:
                credentials = (
                    HTTPAuthorizationCredentials(
                        scheme=parts[0],
                        credentials=parts[1],
                    )
                )

        token = await require_bearer_token(
            credentials=credentials
        )
        actor = await get_current_actor(
            token=token,
            codec=codec,
            identity_service=identity_service,
        )

        context = WebhookAccessContext(
            tenant_id=actor.tenant_id,
            principal_type="user",
            principal_id=actor.user_id,
            api_client_id=None,
            grants=frozenset(
                actor.permissions
            ),
        )

    request.state.webhook_access_context = (
        context
    )
    return context


def require_webhook_access(
    grant: str,
    *,
    context_dependency=(
        get_webhook_access_context
    ),
):
    normalized_grant = (
        grant.strip().lower()
        if isinstance(grant, str)
        else ""
    )

    if normalized_grant not in {
        "webhooks.read",
        "webhooks.write",
    }:
        raise ValueError(
            "Webhook access grant is invalid."
        )

    async def webhook_access_dependency(
        context: Annotated[
            WebhookAccessContext,
            Depends(context_dependency),
        ],
    ) -> WebhookAccessContext:
        if normalized_grant in context.grants:
            return context

        if context.principal_type == "api_key":
            code = "api_key_scope_required"
            message = (
                "API Key scope is required."
            )
        else:
            code = "permission_required"
            message = (
                "Required permission "
                "is not available."
            )

        raise ApiHttpError(
            status_code=403,
            code=code,
            message=message,
        )

    return webhook_access_dependency


def require_permission(
    permission_code: str,
    *,
    actor_dependency=get_current_actor,
):
    normalized_permission = (
        permission_code.strip().lower()
        if isinstance(permission_code, str)
        else ""
    )

    if not normalized_permission:
        raise ValueError(
            "Permission code is required."
        )

    async def permission_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
    ) -> ApiActorContext:
        if (
            normalized_permission
            not in actor.permissions
        ):
            raise ApiHttpError(
                status_code=403,
                code="permission_required",
                message=(
                    "Required permission "
                    "is not available."
                ),
            )

        return actor

    return permission_dependency


def require_module(
    module_code: str,
    *,
    actor_dependency=get_current_actor,
    service_dependency=(
        get_tenant_module_access_service
    ),
):
    normalized_module = (
        module_code.strip().lower()
        if isinstance(module_code, str)
        else ""
    )

    if not normalized_module:
        raise ValueError(
            "Module code is required."
        )

    async def module_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
        service: Annotated[
            TenantModuleAccessService,
            Depends(service_dependency),
        ],
    ) -> ApiActorContext:
        try:
            await service.require_enabled(
                tenant_id=actor.tenant_id,
                module_code=(
                    normalized_module
                ),
            )
        except ModuleDisabledError as exc:
            raise ApiHttpError(
                status_code=403,
                code="module_disabled",
                message=(
                    "Required module "
                    "is not available."
                ),
            ) from exc

        return actor

    return module_dependency


def require_suite(
    suite_code: str,
    *,
    actor_dependency=get_current_actor,
    service_dependency=(
        get_tenant_suite_access_service
    ),
):
    normalized_suite = (
        suite_code.strip().lower()
        if isinstance(suite_code, str)
        else ""
    )

    if not normalized_suite:
        raise ValueError(
            "Suite code is required."
        )

    async def suite_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
        service: Annotated[
            TenantSuiteAccessService,
            Depends(service_dependency),
        ],
    ) -> ApiActorContext:
        try:
            await service.require_enabled(
                tenant_id=actor.tenant_id,
                suite_code=normalized_suite,
            )
        except SuiteDisabledError as exc:
            raise ApiHttpError(
                status_code=403,
                code="module_disabled",
                message=(
                    "Required Suite "
                    "is not available."
                ),
            ) from exc

        return actor

    return suite_dependency


async def require_admin_actor(
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
) -> ApiActorContext:
    if ADMIN_API_ROLES.isdisjoint(
        actor.roles
    ):
        raise ApiHttpError(
            status_code=403,
            code="admin_role_required",
            message=(
                "Administrative role "
                "is required."
            ),
        )

    return actor


def require_admin_permission(
    permission_code: str,
    *,
    actor_dependency=get_current_actor,
):
    normalized_permission = (
        permission_code.strip().lower()
        if isinstance(permission_code, str)
        else ""
    )

    if not normalized_permission:
        raise ValueError(
            "Permission code is required."
        )

    async def admin_permission_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
    ) -> ApiActorContext:
        await require_admin_actor(actor)

        if (
            normalized_permission
            not in actor.permissions
        ):
            raise ApiHttpError(
                status_code=403,
                code="permission_required",
                message=(
                    "Required permission "
                    "is not available."
                ),
            )

        return actor

    return admin_permission_dependency


async def require_super_admin_actor(
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
) -> ApiActorContext:
    if "super_admin" not in actor.roles:
        raise ApiHttpError(
            status_code=403,
            code=(
                "super_admin_role_required"
            ),
            message=(
                "Super Admin role "
                "is required."
            ),
        )

    return actor


def require_super_admin_permission(
    permission_code: str,
    *,
    actor_dependency=get_current_actor,
):
    normalized_permission = (
        permission_code.strip().lower()
        if isinstance(permission_code, str)
        else ""
    )

    if not normalized_permission:
        raise ValueError(
            "Permission code is required."
        )

    async def super_admin_permission_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
    ) -> ApiActorContext:
        await require_super_admin_actor(
            actor
        )

        if (
            normalized_permission
            not in actor.permissions
        ):
            raise ApiHttpError(
                status_code=403,
                code="permission_required",
                message=(
                    "Required permission "
                    "is not available."
                ),
            )

        return actor

    return super_admin_permission_dependency


async def require_specialist_actor(
    actor: Annotated[
        ApiActorContext,
        Depends(get_current_actor),
    ],
) -> ApiActorContext:
    if "specialist" not in actor.roles:
        raise ApiHttpError(
            status_code=403,
            code="specialist_role_required",
            message=(
                "Specialist role is required."
            ),
        )

    return actor


from api.security import RefreshTokenCodec
from api.settings import (
    ApiTelegramAuthSettings,
)
from api.telegram_auth import (
    TelegramInitDataVerifier,
)
from services.api_auth import ApiAuthService
from services.api_auth_factory import (
    build_api_auth_service,
    build_api_email_auth_service,
    build_email_challenge_delivery,
)
from services.api_email_auth import (
    ApiEmailAuthService,
)
from services.email_delivery import (
    ApiEmailChallengeDelivery,
)


@lru_cache(maxsize=1)
def get_refresh_token_codec(
) -> RefreshTokenCodec:
    return RefreshTokenCodec()


@lru_cache(maxsize=1)
def get_telegram_init_data_verifier(
) -> TelegramInitDataVerifier:
    settings = (
        ApiTelegramAuthSettings.from_env()
    )

    return TelegramInitDataVerifier(
        bot_token=settings.bot_token,
        max_age_seconds=(
            settings.max_age_seconds
        ),
    )


def get_api_auth_service(
    session=Depends(get_api_session),
    telegram_verifier=Depends(
        get_telegram_init_data_verifier
    ),
    access_token_codec=Depends(
        get_access_token_codec
    ),
    refresh_token_codec=Depends(
        get_refresh_token_codec
    ),
) -> ApiAuthService:
    return build_api_auth_service(
        session=session,
        telegram_verifier=(
            telegram_verifier
        ),
        access_token_codec=(
            access_token_codec
        ),
        refresh_token_codec=(
            refresh_token_codec
        ),
    )


@lru_cache(maxsize=1)
def get_email_challenge_delivery(
) -> ApiEmailChallengeDelivery:
    return build_email_challenge_delivery()


def get_api_email_auth_service(
    session=Depends(get_api_session),
    email_delivery=Depends(
        get_email_challenge_delivery
    ),
    access_token_codec=Depends(
        get_access_token_codec
    ),
    refresh_token_codec=Depends(
        get_refresh_token_codec
    ),
) -> ApiEmailAuthService:
    return build_api_email_auth_service(
        session=session,
        email_delivery=email_delivery,
        access_token_codec=(
            access_token_codec
        ),
        refresh_token_codec=(
            refresh_token_codec
        ),
    )



def require_admin_any_permission(
    *permission_codes: str,
    actor_dependency=get_current_actor,
):
    normalized_permissions = frozenset(
        permission_code.strip().lower()
        for permission_code
        in permission_codes
        if (
            isinstance(permission_code, str)
            and permission_code.strip()
        )
    )

    if not normalized_permissions:
        raise ValueError(
            "At least one permission code "
            "is required."
        )

    async def admin_any_permission_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
    ) -> ApiActorContext:
        await require_admin_actor(actor)

        if normalized_permissions.isdisjoint(
            actor.permissions
        ):
            raise ApiHttpError(
                status_code=403,
                code="permission_required",
                message=(
                    "Required permission "
                    "is not available."
                ),
            )

        return actor

    return admin_any_permission_dependency



def require_partner_api_scope(
    scope: str,
    principal_dependency=(
        get_partner_api_principal
    ),
):
    validated_scope = (
        validate_api_key_scopes(
            (scope,)
        )[0]
    )

    async def dependency(
        principal: Annotated[
            PartnerApiPrincipal,
            Depends(principal_dependency),
        ],
    ) -> PartnerApiPrincipal:
        if (
            validated_scope
            not in principal.scopes
        ):
            raise ApiHttpError(
                status_code=403,
                code=(
                    "api_key_scope_required"
                ),
                message=(
                    "API Key scope is required."
                ),
            )

        return principal

    return dependency


def require_finance_permission(
    permission_code: str,
    *,
    actor_dependency=get_current_actor,
):
    normalized_permission = (
        permission_code.strip().lower()
        if isinstance(permission_code, str)
        else ""
    )

    if not normalized_permission.startswith(
        "finance."
    ):
        raise ValueError(
            "Finance permission code is required."
        )

    async def finance_permission_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
    ) -> ApiActorContext:
        if "finance_admin" not in actor.roles:
            raise ApiHttpError(
                status_code=403,
                code=(
                    "finance_admin_role_required"
                ),
                message=(
                    "Finance administrator role "
                    "is required."
                ),
            )

        if (
            normalized_permission
            not in actor.permissions
        ):
            raise ApiHttpError(
                status_code=403,
                code="permission_required",
                message=(
                    "Required permission is not "
                    "available."
                ),
            )

        return actor

    return finance_permission_dependency



def require_admin_api_rate_limit(
    *,
    actor_dependency=get_current_actor,
    service_dependency=None,
):
    if service_dependency is None:
        from api.dependencies import (
            get_api_request_rate_limit_service,
        )

        service_dependency = (
            get_api_request_rate_limit_service
        )

    async def admin_api_rate_limit_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
        service: Annotated[
            object,
            Depends(service_dependency),
        ],
    ) -> ApiActorContext:
        from services.api_rate_limits import (
            ApiRequestRateLimitExceededError,
        )

        try:
            await service.ensure_admin_api_allowed(
                tenant_id=actor.tenant_id,
                actor_user_id=actor.user_id,
            )
        except ApiRequestRateLimitExceededError:
            raise ApiHttpError(
                status_code=429,
                code="RATE_LIMIT_EXCEEDED",
                message="Rate limit exceeded.",
            ) from None

        return actor

    return admin_api_rate_limit_dependency


def require_partner_api_rate_limit(
    *,
    principal_dependency=(
        get_partner_api_principal
    ),
    service_dependency=None,
):
    if service_dependency is None:
        from api.dependencies import (
            get_api_request_rate_limit_service,
        )

        service_dependency = (
            get_api_request_rate_limit_service
        )

    async def partner_api_rate_limit_dependency(
        principal: Annotated[
            PartnerApiPrincipal,
            Depends(principal_dependency),
        ],
        service: Annotated[
            object,
            Depends(service_dependency),
        ],
    ) -> PartnerApiPrincipal:
        from services.api_rate_limits import (
            ApiRequestRateLimitExceededError,
        )

        try:
            await service.ensure_partner_api_allowed(
                tenant_id=principal.tenant_id,
                api_key_id=principal.api_key_id,
            )
        except ApiRequestRateLimitExceededError:
            raise ApiHttpError(
                status_code=429,
                code="RATE_LIMIT_EXCEEDED",
                message="Rate limit exceeded.",
            ) from None

        return principal

    return partner_api_rate_limit_dependency


def require_authenticated_user_rate_limit(
    *,
    actor_dependency=get_current_actor,
    service_dependency=None,
):
    if service_dependency is None:
        from api.dependencies import (
            get_api_request_rate_limit_service,
        )

        service_dependency = (
            get_api_request_rate_limit_service
        )

    async def authenticated_user_rate_limit_dependency(
        actor: Annotated[
            ApiActorContext,
            Depends(actor_dependency),
        ],
        service: Annotated[
            object,
            Depends(service_dependency),
        ],
    ) -> ApiActorContext:
        from services.api_rate_limits import (
            ApiRequestRateLimitExceededError,
        )

        try:
            await (
                service
                .ensure_authenticated_user_allowed(
                    tenant_id=actor.tenant_id,
                    user_id=actor.user_id,
                )
            )
        except ApiRequestRateLimitExceededError:
            raise ApiHttpError(
                status_code=429,
                code="RATE_LIMIT_EXCEEDED",
                message="Rate limit exceeded.",
            ) from None

        return actor

    return authenticated_user_rate_limit_dependency


def require_webhook_management_rate_limit(
    *,
    context_dependency=(
        get_webhook_access_context
    ),
    service_dependency=None,
):
    if service_dependency is None:
        from api.dependencies import (
            get_api_request_rate_limit_service,
        )

        service_dependency = (
            get_api_request_rate_limit_service
        )

    async def webhook_rate_limit_dependency(
        context: Annotated[
            WebhookAccessContext,
            Depends(context_dependency),
        ],
        service: Annotated[
            object,
            Depends(service_dependency),
        ],
    ) -> WebhookAccessContext:
        from services.api_rate_limits import (
            ApiRequestRateLimitExceededError,
        )

        try:
            await (
                service
                .ensure_webhook_management_allowed(
                    tenant_id=context.tenant_id,
                    principal_type=(
                        context.principal_type
                    ),
                    principal_id=(
                        context.principal_id
                    ),
                )
            )
        except ApiRequestRateLimitExceededError:
            raise ApiHttpError(
                status_code=429,
                code="RATE_LIMIT_EXCEEDED",
                message="Rate limit exceeded.",
            ) from None

        return context

    return webhook_rate_limit_dependency
