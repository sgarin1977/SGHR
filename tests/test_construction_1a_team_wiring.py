import pytest

from database.repositories.construction_team import ConstructionTeamRepository
from database.repositories.user import UserRepository


def test_c004_team_factory_separates_construction_and_platform_sessions():
    from services.construction_team import build_construction_team_service

    construction_session = object()
    platform_session = object()
    service = build_construction_team_service(
        construction_session=construction_session,
        platform_session=platform_session,
    )
    assert isinstance(service.repository, ConstructionTeamRepository)
    assert isinstance(service.identity_reader, UserRepository)
    assert service.repository.session is construction_session
    assert service.identity_reader.session is platform_session
    assert service.repository.session is not service.identity_reader.session


@pytest.mark.parametrize("invalid", ["construction", "platform", "same"])
def test_c004_team_factory_rejects_invalid_session_wiring(invalid):
    from services.construction_team import build_construction_team_service

    construction_session = object()
    platform_session = object()
    if invalid == "construction":
        construction_session = None
    elif invalid == "platform":
        platform_session = None
    else:
        platform_session = construction_session

    with pytest.raises(ValueError):
        build_construction_team_service(
            construction_session=construction_session,
            platform_session=platform_session,
        )
