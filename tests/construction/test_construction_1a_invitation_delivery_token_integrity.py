from uuid import uuid4

import pytest
from cryptography.fernet import Fernet


@pytest.mark.parametrize("failure", ["tampered", "wrong_key"])
def test_c005_delivery_token_rejects_invalid_encrypted_material(failure):
    from services.construction.construction_invitation_delivery import (
        ConstructionInvitationDeliveryTokenError,
        ConstructionInvitationTokenCodec,
    )

    key = Fernet.generate_key().decode("ascii")
    codec = ConstructionInvitationTokenCodec(encryption_key=key)
    tenant_id, invitation_id = uuid4(), uuid4()
    token = "private-token-must-not-appear-in-errors"
    original = codec.encrypt(
        tenant_id=tenant_id, invitation_id=invitation_id, token=token,
    )
    ciphertext = original
    reader = codec

    if failure == "tampered":
        replacement = "A" if original[10] != "A" else "B"
        ciphertext = original[:10] + replacement + original[11:]
    else:
        reader = ConstructionInvitationTokenCodec(
            encryption_key=Fernet.generate_key().decode("ascii"),
        )

    with pytest.raises(ConstructionInvitationDeliveryTokenError) as error:
        reader.decrypt(
            ciphertext=ciphertext,
            tenant_id=tenant_id, invitation_id=invitation_id,
        )
    assert token not in str(error.value)
    assert ciphertext not in str(error.value)
    assert error.value.__cause__ is None
    assert codec.decrypt(
        ciphertext=original,
        tenant_id=tenant_id, invitation_id=invitation_id,
    ) == token
