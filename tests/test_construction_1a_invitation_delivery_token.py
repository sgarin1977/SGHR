from uuid import uuid4

import pytest
from cryptography.fernet import Fernet


@pytest.mark.parametrize("scope", ["own", "foreign_tenant", "other_invitation"])
def test_c005_delivery_token_is_encrypted_and_bound_to_invitation(scope):
    from services.construction_invitation_delivery import (
        ConstructionInvitationDeliveryTokenError,
        ConstructionInvitationTokenCodec,
    )

    key = Fernet.generate_key().decode("ascii")
    codec = ConstructionInvitationTokenCodec(encryption_key=key)
    tenant_id, invitation_id = uuid4(), uuid4()
    token = "private-invitation-token"

    ciphertext = codec.encrypt(
        tenant_id=tenant_id, invitation_id=invitation_id, token=token,
    )
    assert isinstance(ciphertext, str)
    assert ciphertext
    assert token not in ciphertext

    reader = ConstructionInvitationTokenCodec(encryption_key=key)
    arguments = dict(
        ciphertext=ciphertext,
        tenant_id=uuid4() if scope == "foreign_tenant" else tenant_id,
        invitation_id=uuid4() if scope == "other_invitation" else invitation_id,
    )
    if scope == "own":
        assert reader.decrypt(**arguments) == token
    else:
        with pytest.raises(ConstructionInvitationDeliveryTokenError) as error:
            reader.decrypt(**arguments)
        assert token not in str(error.value)
        assert ciphertext not in str(error.value)
