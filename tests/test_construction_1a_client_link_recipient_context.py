from base64 import urlsafe_b64encode
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from database.repositories.construction_client_link_requests import ConstructionClientLinkRequestState
from services.construction_client_link_request_issuance import ConstructionClientLinkIssuanceResult,hash_client_link_confirmation_token
from services.construction_client_link_confirmation import ConstructionClientLinkConfirmationDeniedError as Denied
from services.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec as Codec
from services.construction_invitation_recipient_context import ConstructionInvitationRecipientContextCodec

NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
TOKEN='ccl1_'+urlsafe_b64encode(bytes(range(32))).decode('ascii').rstrip('=')


@pytest.fixture
def envelope():
    key=Fernet.generate_key().decode('ascii')
    state=ConstructionClientLinkRequestState(uuid4(),uuid4(),uuid4(),uuid4(),uuid4(),None,1,
        hash_client_link_confirmation_token(TOKEN),NOW,NOW+timedelta(minutes=2),None,NOW,1)
    material=ConstructionClientLinkIssuanceResult(state,TOKEN,uuid4())
    codec=Codec(encryption_key=key)
    request=codec.issue_for_request(material=material)
    return SimpleNamespace(key=key,state=state,material=material,codec=codec,request=request)


def test_context_authenticates_route_target_and_version_without_storing_raw_token(envelope):
    c=envelope
    route=c.codec.resolve(recipient_context=c.request.recipient_context,token=TOKEN)
    assert (route.tenant_id,route.client_id,route.request_id,route.target_user_id,route.expected_request_row_version)==(
        c.state.tenant_id,c.state.client_id,c.state.request_id,c.state.target_user_id,1)
    payload=json.loads(Fernet(c.key.encode()).decrypt(c.request.recipient_context.encode()))
    assert payload['purpose']==c.codec.PURPOSE and payload['token_hash']==c.state.token_hash
    assert TOKEN not in json.dumps(payload) and 'token' not in payload
    assert TOKEN not in repr(c.request) and c.request.recipient_context not in repr(c.request)
    assert 'expires_at' not in payload  # Stored DB TTL governs fresh consume, not this routing envelope.


def test_context_can_be_reissued_without_changing_normalized_idempotency_identity(envelope):
    c=envelope
    another=c.codec.issue_for_request(material=c.material)
    assert another.recipient_context!=c.request.recipient_context
    assert c.codec.resolve(recipient_context=another.recipient_context,token=TOKEN)==c.codec.resolve(
        recipient_context=c.request.recipient_context,token=TOKEN)


@pytest.mark.parametrize('kind',['tampered','wrong_key','wrong_token','empty','too_long','wrong_type'])
def test_context_or_secret_tampering_is_denied_without_identity_or_database_access(envelope,kind):
    c=envelope
    context,token,codec=c.request.recipient_context,TOKEN,c.codec
    if kind=='tampered':context=context[:12]+('A' if context[12]!='A' else 'B')+context[13:]
    elif kind=='wrong_key':codec=Codec(encryption_key=Fernet.generate_key().decode())
    elif kind=='wrong_token':token='ccl1_'+urlsafe_b64encode(b'x'*32).decode().rstrip('=')
    elif kind=='empty':context=''
    elif kind=='too_long':context='x'*4097
    else:context={'tenant_id':str(c.state.tenant_id)}
    with pytest.raises(Denied):codec.resolve(recipient_context=context,token=token)


@pytest.mark.parametrize('change',['purpose','uuid','version_bool','version_two','hash','extra','missing'])
def test_authenticated_but_wrong_shape_or_purpose_is_still_denied(envelope,change):
    c=envelope
    f=Fernet(c.key.encode())
    payload=json.loads(f.decrypt(c.request.recipient_context.encode()))
    if change=='purpose':payload['purpose']='construction.invitation.recipient-context.v1'
    elif change=='uuid':payload['tenant_id']='not-a-uuid'
    elif change=='version_bool':payload['expected_request_row_version']=True
    elif change=='version_two':payload['expected_request_row_version']=2
    elif change=='hash':payload['token_hash']='0'*64
    elif change=='extra':payload['confirmed']=True
    else:del payload['target_user_id']
    bad=f.encrypt(json.dumps(payload).encode()).decode()
    with pytest.raises(Denied):c.codec.resolve(recipient_context=bad,token=TOKEN)


def test_server_cannot_issue_context_for_an_unverified_or_already_used_request(envelope):
    c=envelope
    for material in (SimpleNamespace(**vars(c.material)),replace(c.material,audit_id=None),
            replace(c.material,token='ccl1_'+urlsafe_b64encode(b'x'*32).decode().rstrip('=')),
            replace(c.material,request=replace(c.state,row_version=2,consumed_at=NOW))):
        with pytest.raises(ValueError):c.codec.issue_for_request(material=material)


def test_same_key_does_not_make_an_invitation_envelope_a_client_link_context(envelope):
    c=envelope
    invitation=ConstructionInvitationRecipientContextCodec(encryption_key=c.key)
    f=Fernet(c.key.encode())
    old=f.encrypt(json.dumps(dict(purpose=invitation.PURPOSE,tenant_id=str(c.state.tenant_id),
        invitation_id=str(c.state.request_id),token_hash=c.state.token_hash)).encode()).decode()
    with pytest.raises(Denied):c.codec.resolve(recipient_context=old,token=TOKEN)


@pytest.mark.parametrize('key',[None,'','private','ю'*32])
def test_context_key_is_validated_without_exposing_the_key(key):
    with pytest.raises(ValueError) as caught:Codec(encryption_key=key)
    assert str(caught.value)=='Client recipient context key is invalid.'
