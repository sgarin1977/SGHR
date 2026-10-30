"""Compose the existing API for the in-process Telegram transport.

Only the rate-limit backend changes. Authentication, authorization,
module gates, routes and domain services remain the existing API ones.
"""
from services.construction.construction_bot_rate_limit import (
    ConstructionBotPostgresRateLimiter,
)


def create_platform_bot_application(*, session_factory=None):
    from api.app import create_app
    from api.dependencies import get_api_request_rate_limit_service
    from api.routes.platform_bot_entry import get_platform_bot_rates

    if session_factory is None:
        from database.session import get_session
        session_factory = get_session
    if not callable(session_factory):
        raise ValueError("A Platform session factory is required.")

    rates = ConstructionBotPostgresRateLimiter(
        session_factory=session_factory
    )
    application = create_app()

    def rate_backend():
        return rates

    # Backend composition only: never override actor or permission guards.
    application.dependency_overrides[
        get_api_request_rate_limit_service
    ] = rate_backend
    application.dependency_overrides[
        get_platform_bot_rates
    ] = rate_backend

    return application
