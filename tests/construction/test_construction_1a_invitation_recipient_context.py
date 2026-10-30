import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from cryptography.fernet import Fernet
from services.construction.construction_invitation_delivery import ConstructionInvitationTokenCodec
from services.construction.construction_invitation_delivery_material import ConstructionInvitationDeliveryMaterial
from services.construction.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec
from services.construction.construction_invitations import ConstructionInvitationTokenDeniedError, hash_construction_invitation_token

@pytest.fixture
def recipient_case():
    key = Fernet.generate_key().decode('ascii')
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    material = ConstructionInvitationDeliveryMaterial(invitation_id=uuid4(), tenant_id=uuid4(), email='recipient@example.com', expires_at=now + timedelta(days=7), token='private-recipient-token')
    codec = ConstructionInvitationRecipientContextCodec(encryption_key=key)
    request = codec.issue_for_delivery(material=material)
    return SimpleNamespace(key=key, material=material, codec=codec, request=request)

def test_recipient_context_routes_to_server_issued_tenant_and_invitation(recipient_case):
    case = recipient_case
    context = case.codec.resolve(invitation_context=case.request.invitation_context, token=case.request.token)
    assert context.tenant_id == case.material.tenant_id
    assert context.invitation_id == case.material.invitation_id
    assert case.request.token == case.material.token
    assert not hasattr(context, 'token')
    assert not hasattr(context, 'permissions')
    with pytest.raises(FrozenInstanceError):
        context.tenant_id = uuid4()

def test_recipient_request_repr_hides_token_and_context(recipient_case):
    case = recipient_case
    assert case.request.token not in repr(case.request)
    assert case.request.invitation_context not in repr(case.request)
    assert hash_construction_invitation_token(case.request.token) not in repr(case.request)

@pytest.mark.parametrize('bad', [None, '', 7, 'x' * 4097, 'not-a-context', 'é'])
def test_recipient_rejects_malformed_context(recipient_case, bad):
    case = recipient_case
    with pytest.raises(ConstructionInvitationTokenDeniedError) as caught:
        case.codec.resolve(invitation_context=bad, token=case.request.token)
    assert str(caught.value) == 'Invitation context is not available.'

@pytest.mark.parametrize('bad', [None, '', 7, 'different-token'])
def test_recipient_rejects_wrong_one_time_token(recipient_case, bad):
    case = recipient_case
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        case.codec.resolve(invitation_context=case.request.invitation_context, token=bad)

def test_recipient_rejects_tampered_ciphertext(recipient_case):
    case = recipient_case
    original = case.request.invitation_context
    changed = original[:40] + ('A' if original[40] != 'A' else 'B') + original[41:]
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        case.codec.resolve(invitation_context=changed, token=case.request.token)

def test_recipient_rejects_other_server_key(recipient_case):
    case = recipient_case
    other = ConstructionInvitationRecipientContextCodec(encryption_key=Fernet.generate_key().decode('ascii'))
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        other.resolve(invitation_context=case.request.invitation_context, token=case.request.token)

@pytest.mark.parametrize('bad_field', ['purpose', 'tenant_id', 'invitation_id', 'token_hash', 'extra'])
def test_recipient_rejects_invalid_authenticated_payload(recipient_case, bad_field):
    case = recipient_case
    payload = {'purpose': case.codec.PURPOSE, 'tenant_id': str(case.material.tenant_id), 'invitation_id': str(case.material.invitation_id), 'token_hash': hash_construction_invitation_token(case.material.token)}
    if bad_field == 'extra':
        payload['extra'] = 'forbidden'
    else:
        payload[bad_field] = 'invalid'
    context = Fernet(case.key.encode('ascii')).encrypt(json.dumps(payload).encode()).decode()
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        case.codec.resolve(invitation_context=context, token=case.request.token)

def test_recipient_cannot_use_internal_delivery_ciphertext(recipient_case):
    case = recipient_case
    ciphertext = ConstructionInvitationTokenCodec(encryption_key=case.key).encrypt(tenant_id=case.material.tenant_id, invitation_id=case.material.invitation_id, token=case.material.token)
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        case.codec.resolve(invitation_context=ciphertext, token=case.request.token)

def test_recipient_context_cannot_be_used_as_internal_delivery_ciphertext(recipient_case):
    case = recipient_case
    from services.construction.construction_invitation_delivery import ConstructionInvitationDeliveryTokenError
    with pytest.raises(ConstructionInvitationDeliveryTokenError):
        ConstructionInvitationTokenCodec(encryption_key=case.key).decrypt(ciphertext=case.request.invitation_context, tenant_id=case.material.tenant_id, invitation_id=case.material.invitation_id)

def test_locator_does_not_replace_business_expiry_or_token_consumption(recipient_case):
    case = recipient_case
    material = ConstructionInvitationDeliveryMaterial(invitation_id=case.material.invitation_id, tenant_id=case.material.tenant_id, email=case.material.email, expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc), token=case.material.token)
    request = case.codec.issue_for_delivery(material=material)
    context = case.codec.resolve(invitation_context=request.invitation_context, token=request.token)
    assert context.invitation_id == material.invitation_id

@pytest.mark.parametrize('bad', [None, '', 'invalid', 'é'])
def test_recipient_codec_rejects_invalid_key_without_echo(bad):
    with pytest.raises(ValueError) as caught:
        ConstructionInvitationRecipientContextCodec(encryption_key=bad)
    assert str(caught.value) == 'Invitation context encryption key is invalid.'

def test_recipient_issuer_rejects_untrusted_plain_dict(recipient_case):
    case = recipient_case
    with pytest.raises(ValueError):
        case.codec.issue_for_delivery(material={'tenant_id': case.material.tenant_id, 'invitation_id': case.material.invitation_id, 'token': case.material.token})

def test_recipient_cannot_use_saved_response_ciphertext(recipient_case):
    case = recipient_case
    ciphertext = Fernet(case.key.encode('ascii')).encrypt(json.dumps({'id': str(case.material.invitation_id), 'status': 'accepted', 'row_version': 5}).encode()).decode()
    with pytest.raises(ConstructionInvitationTokenDeniedError):
        case.codec.resolve(invitation_context=ciphertext, token=case.request.token)
