import pytest


@pytest.mark.asyncio
async def test_authenticated_user_rate_limit_is_120_per_minute():
    from uuid import uuid4

    from services.api_rate_limits import (
        API_RATE_LIMIT_WINDOW_SECONDS,
        ApiRequestRateLimitExceededError,
        ApiRequestRateLimitService,
    )

    tenant_id = uuid4()
    user_id = uuid4()
    calls = []

    class FakeStore:
        async def increment(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (
                120
                if len(calls) == 1
                else 121
            )

    service = ApiRequestRateLimitService(
        store=FakeStore(),
    )

    count = await (
        service.ensure_authenticated_user_allowed(
            tenant_id=tenant_id,
            user_id=user_id,
        )
    )
    assert count == 120

    with pytest.raises(
        ApiRequestRateLimitExceededError
    ):
        await (
            service.ensure_authenticated_user_allowed(
                tenant_id=tenant_id,
                user_id=user_id,
            )
        )

    assert calls == [
        {
            "key": (
                "api-rate-limit:"
                f"tenant:{tenant_id}:"
                f"authenticated-user:{user_id}"
            ),
            "window_seconds": (
                API_RATE_LIMIT_WINDOW_SECONDS
            ),
        },
        {
            "key": (
                "api-rate-limit:"
                f"tenant:{tenant_id}:"
                f"authenticated-user:{user_id}"
            ),
            "window_seconds": (
                API_RATE_LIMIT_WINDOW_SECONDS
            ),
        },
    ]



@pytest.mark.asyncio
async def test_authenticated_user_rate_limit_guard_uses_verified_actor_scope():
    from uuid import uuid4

    from api.auth import (
        require_authenticated_user_rate_limit,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    user_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=user_id,
        tenant_id=tenant_id,
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeRateLimitService:
        async def ensure_authenticated_user_allowed(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return 1

    service = FakeRateLimitService()

    dependency = (
        require_authenticated_user_rate_limit(
            actor_dependency=lambda: actor,
            service_dependency=lambda: service,
        )
    )

    result = await dependency(
        actor=actor,
        service=service,
    )

    assert result is actor
    assert calls == [
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
        }
    ]



@pytest.mark.asyncio
async def test_authenticated_user_rate_limit_guard_returns_sanitized_429():
    from uuid import uuid4

    from api.auth import (
        require_authenticated_user_rate_limit,
    )
    from api.errors import ApiHttpError
    from services.api_identity import ApiActorContext
    from services.api_rate_limits import (
        ApiRequestRateLimitExceededError,
    )

    actor = ApiActorContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        active_role="client",
        roles=("client",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
    )

    class FakeRateLimitService:
        async def ensure_authenticated_user_allowed(
            self,
            **kwargs,
        ):
            raise ApiRequestRateLimitExceededError(
                "Private Redis counter details.",
                retry_after_seconds=37,
            )

    service = FakeRateLimitService()
    dependency = (
        require_authenticated_user_rate_limit(
            actor_dependency=lambda: actor,
            service_dependency=lambda: service,
        )
    )

    with pytest.raises(ApiHttpError) as captured:
        await dependency(
            actor=actor,
            service=service,
        )

    error = captured.value
    assert error.status_code == 429
    assert error.code == "RATE_LIMIT_EXCEEDED"
    assert error.message == "Rate limit exceeded."
    assert "Private Redis" not in error.message



@pytest.mark.asyncio
async def test_partner_api_rate_limit_is_300_per_minute_per_api_key():
    from uuid import uuid4

    from services.api_rate_limits import (
        API_RATE_LIMIT_WINDOW_SECONDS,
        ApiRequestRateLimitExceededError,
        ApiRequestRateLimitService,
    )

    tenant_id = uuid4()
    api_key_id = uuid4()
    calls = []

    class FakeStore:
        async def increment(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (
                300
                if len(calls) == 1
                else 301
            )

    service = ApiRequestRateLimitService(
        store=FakeStore(),
    )

    count = await (
        service.ensure_partner_api_allowed(
            tenant_id=tenant_id,
            api_key_id=api_key_id,
        )
    )
    assert count == 300

    with pytest.raises(
        ApiRequestRateLimitExceededError
    ):
        await service.ensure_partner_api_allowed(
            tenant_id=tenant_id,
            api_key_id=api_key_id,
        )

    expected = {
        "key": (
            "api-rate-limit:"
            f"tenant:{tenant_id}:"
            f"partner-api-key:{api_key_id}"
        ),
        "window_seconds": (
            API_RATE_LIMIT_WINDOW_SECONDS
        ),
    }
    assert calls == [
        expected,
        expected,
    ]



@pytest.mark.asyncio
async def test_partner_api_rate_limit_guard_uses_verified_api_key_principal():
    from uuid import uuid4

    from api.auth import (
        require_partner_api_rate_limit,
    )
    from services.partner_api_auth import (
        PartnerApiPrincipal,
    )

    tenant_id = uuid4()
    api_client_id = uuid4()
    api_key_id = uuid4()
    calls = []

    principal = PartnerApiPrincipal(
        tenant_id=tenant_id,
        api_client_id=api_client_id,
        api_key_id=api_key_id,
        scopes=frozenset(
            {"specialists.read"}
        ),
    )

    class FakeRateLimitService:
        async def ensure_partner_api_allowed(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return 1

    service = FakeRateLimitService()

    dependency = require_partner_api_rate_limit(
        principal_dependency=lambda: principal,
        service_dependency=lambda: service,
    )

    result = await dependency(
        principal=principal,
        service=service,
    )

    assert result is principal
    assert calls == [
        {
            "tenant_id": tenant_id,
            "api_key_id": api_key_id,
        }
    ]



@pytest.mark.asyncio
async def test_admin_api_rate_limit_is_120_per_minute_per_actor():
    from uuid import uuid4

    from services.api_rate_limits import (
        API_RATE_LIMIT_WINDOW_SECONDS,
        ApiRequestRateLimitExceededError,
        ApiRequestRateLimitService,
    )

    tenant_id = uuid4()
    actor_user_id = uuid4()
    calls = []

    class FakeStore:
        async def increment(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (
                120
                if len(calls) == 1
                else 121
            )

    service = ApiRequestRateLimitService(
        store=FakeStore(),
    )

    count = await service.ensure_admin_api_allowed(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
    )
    assert count == 120

    with pytest.raises(
        ApiRequestRateLimitExceededError
    ):
        await service.ensure_admin_api_allowed(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
        )

    expected = {
        "key": (
            "api-rate-limit:"
            f"tenant:{tenant_id}:"
            f"admin-actor:{actor_user_id}"
        ),
        "window_seconds": (
            API_RATE_LIMIT_WINDOW_SECONDS
        ),
    }
    assert calls == [
        expected,
        expected,
    ]



@pytest.mark.asyncio
async def test_admin_api_rate_limit_guard_uses_verified_actor_scope():
    from uuid import uuid4

    from api.auth import (
        require_admin_api_rate_limit,
    )
    from services.api_identity import ApiActorContext

    tenant_id = uuid4()
    actor_user_id = uuid4()
    calls = []

    actor = ApiActorContext(
        user_id=actor_user_id,
        tenant_id=tenant_id,
        active_role="admin",
        roles=("admin",),
        language_code="uk",
        timezone="Europe/Kyiv",
        status="active",
        permissions=("admin.users.read",),
    )

    class FakeRateLimitService:
        async def ensure_admin_api_allowed(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return 1

    service = FakeRateLimitService()

    dependency = require_admin_api_rate_limit(
        actor_dependency=lambda: actor,
        service_dependency=lambda: service,
    )

    result = await dependency(
        actor=actor,
        service=service,
    )

    assert result is actor
    assert calls == [
        {
            "tenant_id": tenant_id,
            "actor_user_id": actor_user_id,
        }
    ]



def test_all_admin_api_routes_use_admin_actor_rate_limit_guard():
    from api.routes.admin_audit import (
        router as audit_router,
    )
    from api.routes.admin_complaints import (
        router as complaints_router,
    )
    from api.routes.admin_reviews import (
        router as reviews_router,
    )
    from api.routes.admin_specialists import (
        router as specialists_router,
    )
    from api.routes.admin_users import (
        router as users_router,
    )

    routers = (
        users_router,
        specialists_router,
        reviews_router,
        complaints_router,
        audit_router,
    )

    admin_routes = [
        route
        for router in routers
        for route in router.routes
        if (
            isinstance(
                getattr(route, "path", None),
                str,
            )
            and route.path.startswith("/admin/")
        )
    ]

    assert admin_routes, (
        "No Admin API routes are registered."
    )

    def dependency_names(dependant):
        names = set()

        for dependency in dependant.dependencies:
            call = getattr(
                dependency,
                "call",
                None,
            )
            name = getattr(
                call,
                "__name__",
                None,
            )
            if name:
                names.add(name)

            names.update(
                dependency_names(dependency)
            )

        return names

    missing = []

    for route in admin_routes:
        names = dependency_names(
            route.dependant
        )

        if (
            "admin_api_rate_limit_dependency"
            not in names
        ):
            for method in sorted(
                route.methods or ()
            ):
                missing.append(
                    (
                        method,
                        route.path,
                    )
                )

    assert missing == []



@pytest.mark.asyncio
async def test_public_api_rate_limit_is_60_per_minute_per_ip():
    from services.api_rate_limits import (
        API_RATE_LIMIT_WINDOW_SECONDS,
        ApiRequestRateLimitExceededError,
        ApiRequestRateLimitService,
    )

    client_ip = "203.0.113.10"
    calls = []

    class FakeStore:
        async def increment(
            self,
            **kwargs,
        ):
            calls.append(kwargs)
            return (
                60
                if len(calls) == 1
                else 61
            )

    service = ApiRequestRateLimitService(
        store=FakeStore(),
    )

    count = await service.ensure_public_api_allowed(
        client_ip=client_ip,
    )
    assert count == 60

    with pytest.raises(
        ApiRequestRateLimitExceededError
    ):
        await service.ensure_public_api_allowed(
            client_ip=client_ip,
        )

    expected = {
        "key": (
            "api-rate-limit:"
            f"public-ip:{client_ip}"
        ),
        "window_seconds": (
            API_RATE_LIMIT_WINDOW_SECONDS
        ),
    }
    assert calls == [
        expected,
        expected,
    ]



def test_client_ip_resolver_trusts_forwarded_chain_only_from_configured_proxy():
    from api.client_ip import resolve_client_ip

    spoofed = resolve_client_ip(
        peer_ip="198.51.100.25",
        forwarded_for=(
            "203.0.113.99"
        ),
        trusted_proxy_networks=(
            "10.0.0.0/8",
        ),
    )
    assert spoofed == "198.51.100.25"

    proxied = resolve_client_ip(
        peer_ip="10.0.0.10",
        forwarded_for=(
            "203.0.113.40, 10.0.0.9"
        ),
        trusted_proxy_networks=(
            "10.0.0.0/8",
        ),
    )
    assert proxied == "203.0.113.40"
