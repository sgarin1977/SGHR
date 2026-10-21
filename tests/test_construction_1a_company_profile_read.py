def test_con_c001_company_profile_get_uses_platform_membership():
    from api.auth import get_current_actor
    from api.routes.company_profiles import (
        get_company_profile_write_context,
        router,
    )

    routes = [
        route
        for route in router.routes
        if getattr(route, "path", None)
        == "/organization/company-profile"
        and "GET" in getattr(route, "methods", ())
    ]
    assert len(routes) == 1
    dependencies = {
        dependency.call
        for dependency in routes[0].dependant.dependencies
    }
    assert get_current_actor in dependencies
    assert get_company_profile_write_context not in dependencies
