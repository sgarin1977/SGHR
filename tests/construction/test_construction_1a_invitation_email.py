import pytest


@pytest.mark.parametrize("value,expected", [
    ("  Person@EXAMPLE.COM  ", "person@example.com"),
    ("First.Last+Team@example.com", "first.last+team@example.com"),
])
def test_c005_invitation_email_matches_platform_identity_key(value, expected):
    from services.construction.construction_invitations import (
        normalize_construction_invitation_email,
    )

    result = normalize_construction_invitation_email(value)
    assert result == expected
    assert normalize_construction_invitation_email(result) == result


@pytest.mark.parametrize("value", [
    None,
    "",
    "   ",
    "not-an-email",
    "person@",
    "first last@example.com",
])
def test_c005_invitation_rejects_invalid_email(value):
    from services.construction.construction_invitations import (
        ConstructionInvitationValidationError,
        normalize_construction_invitation_email,
    )

    with pytest.raises(ConstructionInvitationValidationError):
        normalize_construction_invitation_email(value)
