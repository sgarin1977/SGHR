import pytest


@pytest.mark.parametrize(
    "names,verified_emails,expected",
    [
        ([], [], {"display_name": None, "email": None}),
        (
            ["Nazar"], ["one@example.com"],
            {"display_name": "Nazar", "email": "one@example.com"},
        ),
        (
            ["Nazar", "Other"], ["one@example.com"],
            {"display_name": None, "email": "one@example.com"},
        ),
        (
            ["Nazar"], ["one@example.com", "two@example.com"],
            {"display_name": "Nazar", "email": None},
        ),
        (
            ["", " ", "\t"], [],
            {"display_name": None, "email": None},
        ),
        (
            ["", "Nazar"], [],
            {"display_name": "Nazar", "email": None},
        ),
    ],
)
def test_c004_identity_uses_only_unambiguous_values(
    names, verified_emails, expected,
):
    from services.construction_team import resolve_team_identity

    original_names = list(names)
    original_emails = list(verified_emails)

    result = resolve_team_identity(
        display_names=names,
        verified_emails=verified_emails,
    )
    assert result == expected
    assert names == original_names
    assert verified_emails == original_emails
