from typing import Protocol
from uuid import UUID


WEBHOOK_MANAGEMENT_RATE_LIMIT = 60
AUTHENTICATED_USER_RATE_LIMIT = 120
PARTNER_API_RATE_LIMIT = 300
ADMIN_API_RATE_LIMIT = 120
PUBLIC_API_RATE_LIMIT = 60
API_RATE_LIMIT_WINDOW_SECONDS = 60

WEBHOOK_MANAGEMENT_PRINCIPAL_TYPES = frozenset(
    {
        "user",
        "api_key",
    }
)


class ApiRequestRateLimitExceededError(
    Exception
):
    def __init__(
        self,
        message: str = "API rate limit exceeded.",
        *,
        retry_after_seconds: int = (
            API_RATE_LIMIT_WINDOW_SECONDS
        ),
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = (
            retry_after_seconds
        )


class ApiRequestRateLimitStore(Protocol):
    async def increment(
        self,
        *,
        key: str,
        window_seconds: int,
    ) -> int:
        ...


class ApiRequestRateLimitService:
    def __init__(
        self,
        *,
        store: ApiRequestRateLimitStore,
    ) -> None:
        self.store = store

    async def ensure_webhook_management_allowed(
        self,
        *,
        tenant_id: UUID,
        principal_type: str,
        principal_id: UUID,
    ) -> int:
        normalized_principal_type = (
            principal_type.strip().lower()
        )

        if (
            normalized_principal_type
            not in WEBHOOK_MANAGEMENT_PRINCIPAL_TYPES
        ):
            raise ValueError(
                "Unsupported API rate-limit principal."
            )

        key = (
            "api-rate-limit:"
            f"tenant:{tenant_id}:"
            "webhook-management:"
            f"{normalized_principal_type}:"
            f"{principal_id}"
        )

        current_count = await self.store.increment(
            key=key,
            window_seconds=(
                API_RATE_LIMIT_WINDOW_SECONDS
            ),
        )

        if (
            current_count
            > WEBHOOK_MANAGEMENT_RATE_LIMIT
        ):
            raise ApiRequestRateLimitExceededError()

        return current_count



    async def ensure_authenticated_user_allowed(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> int:
        key = (
            "api-rate-limit:"
            f"tenant:{tenant_id}:"
            f"authenticated-user:{user_id}"
        )

        current_count = await self.store.increment(
            key=key,
            window_seconds=(
                API_RATE_LIMIT_WINDOW_SECONDS
            ),
        )

        if (
            current_count
            > AUTHENTICATED_USER_RATE_LIMIT
        ):
            raise ApiRequestRateLimitExceededError()

        return current_count


    async def ensure_partner_api_allowed(
        self,
        *,
        tenant_id: UUID,
        api_key_id: UUID,
    ) -> int:
        key = (
            "api-rate-limit:"
            f"tenant:{tenant_id}:"
            f"partner-api-key:{api_key_id}"
        )

        current_count = await self.store.increment(
            key=key,
            window_seconds=(
                API_RATE_LIMIT_WINDOW_SECONDS
            ),
        )

        if current_count > PARTNER_API_RATE_LIMIT:
            raise ApiRequestRateLimitExceededError()

        return current_count


    async def ensure_admin_api_allowed(
        self,
        *,
        tenant_id: UUID,
        actor_user_id: UUID,
    ) -> int:
        key = (
            "api-rate-limit:"
            f"tenant:{tenant_id}:"
            f"admin-actor:{actor_user_id}"
        )

        current_count = await self.store.increment(
            key=key,
            window_seconds=(
                API_RATE_LIMIT_WINDOW_SECONDS
            ),
        )

        if current_count > ADMIN_API_RATE_LIMIT:
            raise ApiRequestRateLimitExceededError()

        return current_count


    async def ensure_public_api_allowed(
        self,
        *,
        client_ip: str,
    ) -> int:
        from ipaddress import ip_address

        try:
            normalized_client_ip = str(
                ip_address(
                    str(client_ip).strip()
                )
            )
        except ValueError as exc:
            raise ValueError(
                "Public API client IP is invalid."
            ) from exc

        key = (
            "api-rate-limit:"
            f"public-ip:{normalized_client_ip}"
        )

        current_count = await self.store.increment(
            key=key,
            window_seconds=(
                API_RATE_LIMIT_WINDOW_SECONDS
            ),
        )

        if current_count > PUBLIC_API_RATE_LIMIT:
            raise ApiRequestRateLimitExceededError()

        return current_count


class RedisApiRequestRateLimitStore:
    _INCREMENT_SCRIPT = """
local current = redis.call(
    'INCR',
    KEYS[1]
)
if current == 1 then
    redis.call(
        'EXPIRE',
        KEYS[1],
        ARGV[1]
    )
end
return current
"""

    def __init__(
        self,
        *,
        client,
    ) -> None:
        self.client = client

    async def increment(
        self,
        *,
        key: str,
        window_seconds: int,
    ) -> int:
        current_count = await self.client.eval(
            self._INCREMENT_SCRIPT,
            1,
            key,
            window_seconds,
        )
        return int(current_count)



class InMemoryApiRequestRateLimitStore:
    def __init__(
        self,
        *,
        clock=None,
    ) -> None:
        import asyncio
        import time

        self._clock = clock or time.monotonic
        self._lock = asyncio.Lock()
        self._windows: dict[
            str,
            tuple[float, int],
        ] = {}

    async def increment(
        self,
        *,
        key: str,
        window_seconds: int,
    ) -> int:
        if window_seconds <= 0:
            raise ValueError(
                "Rate-limit window must be positive."
            )

        async with self._lock:
            now = float(self._clock())
            current = self._windows.get(key)

            if (
                current is None
                or now >= current[0]
            ):
                expires_at = (
                    now + window_seconds
                )
                count = 1
            else:
                expires_at = current[0]
                count = current[1] + 1

            self._windows[key] = (
                expires_at,
                count,
            )
            return count


def build_api_request_rate_limit_service(
    *,
    environment: str,
    redis_client=None,
) -> ApiRequestRateLimitService:
    normalized_environment = (
        environment.strip().lower()
        if isinstance(environment, str)
        else ""
    )

    if normalized_environment not in {
        "sandbox",
        "production",
    }:
        raise ValueError(
            "API rate-limit environment "
            "must be sandbox or production."
        )

    if normalized_environment == "production":
        if redis_client is None:
            raise ValueError(
                "Redis client is required for "
                "production API rate limits."
            )

        store = RedisApiRequestRateLimitStore(
            client=redis_client
        )
    elif redis_client is not None:
        store = RedisApiRequestRateLimitStore(
            client=redis_client
        )
    else:
        store = InMemoryApiRequestRateLimitStore()

    return ApiRequestRateLimitService(
        store=store
    )
