"""Local app limiter for Construction HTTP tests; no Redis or environment reads.

Each app receives the real shared limiter backed by its deterministic local
store. Test-specific explicit dependency overrides may replace it afterwards.
"""
from api.dependencies import get_api_request_rate_limit_service
from services.api_rate_limits import ApiRequestRateLimitService, InMemoryApiRequestRateLimitStore


def with_local_construction_rate_limit(app):
    service = ApiRequestRateLimitService(store=InMemoryApiRequestRateLimitStore())
    app.dependency_overrides.setdefault(get_api_request_rate_limit_service, lambda: service)
    return app


def construction_module_dependencies(router):
    # Fixture overrides must select Suite/Core gates by their purpose rather
    # than offsets that change whenever a new shared security guard is added.
    names = ('suite_dependency', 'module_dependency')
    result = [dependency for dependency in router.dependencies
              if getattr(dependency.dependency, '__name__', None) in names]
    assert [item.dependency.__name__ for item in result] == list(names)
    return result
