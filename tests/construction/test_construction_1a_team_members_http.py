def test_c004_member_route_is_read_only_guarded_and_uses_both_sessions():
    import api.routes.construction as routes
    from api.auth import get_current_actor
    from api.dependencies import get_api_session

    matches = [
        route for route in routes.router.routes
        if getattr(route, "path", None) == "/construction/team/members"
    ]
    assert len(matches) == 1
    route = matches[0]
    assert route.methods == {"GET"}

    dependencies = route.dependant.dependencies
    calls = {dependency.call for dependency in dependencies}
    assert get_current_actor in calls
    assert routes.get_construction_access_context in calls
    assert any(
        getattr(call, "construction_permission_code", None)
        == "construction.team.manage"
        for call in calls
    )

    service_dependency = next(
        dependency for dependency in dependencies
        if dependency.call is routes.get_team_member_service
    )
    session_calls = {
        dependency.call
        for dependency in service_dependency.dependencies
    }
    assert routes.get_construction_scoped_session in session_calls
    assert get_api_session in session_calls
