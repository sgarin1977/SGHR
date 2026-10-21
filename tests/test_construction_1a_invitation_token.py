def test_c005_invitation_tokens_are_distinct_and_hash_only():
    from services.construction_invitations import (
        generate_construction_invitation_token,
        hash_construction_invitation_token,
    )

    first = generate_construction_invitation_token()
    second = generate_construction_invitation_token()

    assert isinstance(first, str)
    assert len(first) >= 32
    assert first != second

    first_hash = hash_construction_invitation_token(first)
    second_hash = hash_construction_invitation_token(second)

    assert len(first_hash) == 64
    assert all(char in "0123456789abcdef" for char in first_hash)
    assert first_hash != first
    assert first_hash != second_hash
    assert hash_construction_invitation_token(first) == first_hash


def test_c005_invitation_token_verification_rejects_other_material():
    from services.construction_invitations import (
        generate_construction_invitation_token,
        hash_construction_invitation_token,
        verify_construction_invitation_token,
    )

    token = generate_construction_invitation_token()
    expected_hash = hash_construction_invitation_token(token)

    assert verify_construction_invitation_token(
        token=token, expected_hash=expected_hash,
    ) is True

    for invalid_token in (
        generate_construction_invitation_token(),
        token + " ",
        "",
        None,
    ):
        assert verify_construction_invitation_token(
            token=invalid_token, expected_hash=expected_hash,
        ) is False

    for invalid_hash in ("", "invalid", None):
        assert verify_construction_invitation_token(
            token=token, expected_hash=invalid_hash,
        ) is False
