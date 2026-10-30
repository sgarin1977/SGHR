from construction_rate_limit_fixture import with_local_construction_rate_limit
"""Approved operator-request HTTP with real local SQL workflows and replay."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from cryptography.fernet import Fernet
import httpx
import pytest
from sqlalchemy import insert, select, text, update

import api.routes.construction_client_link_requests as routes
import api.routes.construction_client_confirmation as confirmation_routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_api_session, get_api_request_rate_limit_service, get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.platform_principal import get_authenticated_platform_principal
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from api.settings import ApiConfigurationError
from database.models import ConstructionClient, ConstructionClientUserLinkRequest as Request
from services.api_identity import ApiIdentityAccessError
from services.api_platform_principal import ApiPlatformPrincipal
from services.api_rate_limits import ApiRequestRateLimitExceededError
from services.construction.construction_client_link_confirmation import ConstructionClientLinkConfirmationDeniedError as Denied
from services.construction.construction_client_link_confirmation_workflow import ConstructionClientLinkIdempotencyInProgressError
from services.construction.construction_client_link_recipient_context import ConstructionClientLinkRecipientContextCodec as Codec
from services.construction.construction_client_link_request_issuance import ConstructionClientLinkIssuanceResult, hash_client_link_confirmation_token
from services.construction.construction_clients import ConstructionClientOperationError, ConstructionClientValidationError
from services.construction.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_client_link_issuance_workflow import issuance_pipeline, snapshot
from test_construction_client_link_email_queue import queued, jobs
from test_construction_client_link_exact_email import email_pipeline
from test_construction_1a_client_link_confirmation_workflow import pipeline
from test_construction_1a_client_link_confirmation import sql_confirmation, seed_request, TOKEN
from test_construction_1a_client_link_request_repository import request_repo
from test_construction_1a_client_link_request_model import requests_db
from test_construction_01_project_read_http import walk_routes


def supplied(value):return lambda:value


@pytest.fixture
async def operator_http(queued):
    p,c=queued,queued.c
    c.foreign_client=uuid4()
    c.sync.execute(insert(ConstructionClient.__table__).values(id=c.foreign_client,tenant_id=c.foreign,
        display_name='Foreign fixture client',client_type='person'))
    c.sync.commit()
    p.workflow.request_email_link=AsyncMock(wraps=p.workflow.request_email_link)
    p.workflow.request_replacement=AsyncMock(wraps=p.workflow.request_replacement)
    p.context_codec=Codec(encryption_key=p.key)
    p.runtime=routes.ClientLinkRequestRuntime(p.workflow,p.context_codec)
    p.suite,p.module=SimpleNamespace(require_enabled=AsyncMock()),SimpleNamespace(require_enabled=AsyncMock())
    p.rate=SimpleNamespace(ensure_authenticated_user_allowed=AsyncMock())
    p.app=with_local_construction_rate_limit(create_app())
    for dep,value in ((get_current_actor,c.actor),(get_construction_access_context,c.context),
        (get_construction_scoped_session,c.session),(get_api_session,p.issue_identity.session),
        (get_tenant_suite_access_service,p.suite),(get_tenant_module_access_service,p.module),
        (get_api_request_rate_limit_service,p.rate)):
        p.app.dependency_overrides[dep]=supplied(value)
    p.app.dependency_overrides[routes.get_client_link_request_runtime]=lambda:p.runtime
    return p


async def post(p,body=None,*,identifier=None,key='operator-http-key',params=None,headers=None):
    c=p.c
    if not c.sync.in_transaction():c.sync.begin()
    request_headers=[('X-Request-ID','operator-request-http'),('X-Tenant-ID',str(c.foreign))]
    if key is not None:request_headers.append(('Idempotency-Key',key))
    if headers:request_headers.extend(headers)
    path='/api/v1/construction/clients/'+(identifier or str(c.client))+'/user-link/requests'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=p.app,raise_app_exceptions=False),base_url='http://testserver') as client:
        return await client.post(path,json=body if body is not None else {
            'target_email':'person@example.com','expected_row_version':1},headers=request_headers,params=params)


def recipient_material(p, data):
    # Test-only reconstruction of what the email channel will receive.
    # Operator HTTP responses no longer expose these secrets.
    for record in p.ledger.all():
        saved = p.codec.decrypt(record.response_ciphertext)
        if saved["material"]["request"]["request_id"] != data["request_id"]:
            continue
        token = p.deriver.derive(
            reservation_id=record.id, reserved_at=record.created_at,
            tenant_id=record.tenant_id, initiated_by_user_id=record.principal_id,
            payload=saved["email_delivery"]["command"],
        )
        material = p.workflow._material(saved["material"], token)
        envelope = p.context_codec.issue_replayable_for_request(material=material)
        return {
            "recipient_context": envelope.recipient_context,
            "token": envelope.token,
        }
    raise AssertionError("Email receipt not found.")


def client_state(c):
    return c.sync.execute(select(ConstructionClient.linked_user_id,ConstructionClient.row_version)
        .where(ConstructionClient.id==c.client)).one()


@pytest.mark.asyncio
async def test_approved_request_http_creates_pending_confirmation_without_linking_user(operator_http):
    p, c = operator_http, operator_http.c
    users = c.sync.execute(text("SELECT * FROM users ORDER BY id")).all()
    response = await post(p)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert set(data) == {
        "request_id", "tenant_id", "client_id", "target_user_id",
        "row_version", "issued_at", "expires_at", "delivery_status",
    }
    assert data["client_id"] == str(c.client)
    assert data["tenant_id"] == str(c.tenant)
    assert data["target_user_id"] == str(c.target)
    assert data["delivery_status"] == "queued" and data["row_version"] == 1
    assert data["issued_at"] == c.now.isoformat().replace("+00:00", "Z")
    assert data["expires_at"] == (c.now + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["meta"] == {}
    assert response.json()["request_id"] == "operator-request-http"

    delivered = recipient_material(p, data)
    resolved = p.context_codec.resolve(**delivered)
    assert (
        str(resolved.request_id), resolved.client_id,
        resolved.tenant_id, resolved.target_user_id,
    ) == (data["request_id"], c.client, c.tenant, c.target)
    assert delivered["token"] not in response.text
    assert delivered["recipient_context"] not in response.text
    assert client_state(c) == (None, 1)
    assert c.sync.execute(text("SELECT * FROM users ORDER BY id")).all() == users
    assert c.sync.get(Request, c.request_id).invalidated_at is not None
    assert c.sync.execute(text("SELECT count(*) FROM request_repo_test_audit")).scalar_one() == 1
    audit = c.audit_rows[-1]
    assert str(audit.entity_id) == data["request_id"]
    assert audit.payload["invalidated_request_ids"] == [str(c.request_id)]
    assert audit.trace_id == "operator-request-http"
    receipt = p.codec.decrypt(p.ledger.all()[0].response_ciphertext)
    assert delivered["token"] not in json.dumps(receipt)
    assert delivered["recipient_context"] not in json.dumps(receipt)
    assert "token_hash" not in receipt["material"]["request"]
    assert delivered["token"] not in json.dumps(audit.payload)
    assert hash_client_link_confirmation_token(delivered["token"]) not in response.text
    assert "audit_id" not in response.text and "token_hash" not in response.text
    assert len(jobs(p)) == 1
    p.queue.enqueue.assert_awaited_once()
    p.email_reader.find_verified_user_id.assert_awaited_once_with(email="person@example.com")
    p.rate.ensure_authenticated_user_allowed.assert_awaited_once_with(
        tenant_id=c.tenant, user_id=c.operator,
    )
    p.suite.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, suite_code="construction",
    )
    p.module.require_enabled.assert_awaited_once_with(
        tenant_id=c.tenant, module_code="construction.core",
    )
    assert c.session.commit.await_count == 1


@pytest.mark.asyncio
async def test_http_replay_keeps_exact_secret_context_expiry_and_does_not_invalidate_newer_issue(operator_http):
    p,c=operator_http,operator_http.c
    first=await post(p)
    c.now+=timedelta(seconds=1)
    newer=await post(p,key='new-http-request')
    assert newer.status_code==201 and newer.json()['data']['request_id']!=first.json()['data']['request_id']
    before=snapshot(p)
    replay=await post(p)
    assert replay.status_code==201 and replay.json()==first.json()
    assert snapshot(p)==before and len(c.audit_rows)==2
    assert p.workflow.request_email_link.await_count==3  # workflow itself authorizes replay.
    envelope=recipient_material(p, first.json()['data'])
    old_route=p.context_codec.resolve(recipient_context=envelope['recipient_context'],token=envelope['token'])
    assert c.sync.get(Request,old_route.request_id).invalidated_at is not None


@pytest.mark.asyncio
async def test_http_replay_after_expiry_returns_original_envelope_without_renewal(operator_http):
    p,c=operator_http,operator_http.c
    first=await post(p)
    ledger=p.ledger.all()[0]
    ledger.expires_at=c.now+timedelta(days=2);p.ledger.save(ledger);c.sync.commit()
    c.now+=timedelta(hours=25)
    before=snapshot(p)
    replay=await post(p)
    assert replay.status_code==201 and replay.json()==first.json() and snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('changed',['target','client','version','previous'])
async def test_reused_key_with_changed_payload_is_409_without_second_issue(operator_http,changed):
    p,c=operator_http,operator_http.c
    assert (await post(p)).status_code==201
    before=snapshot(p)
    body=dict(target_email='person@example.com',expected_row_version=1);options={}
    if changed=='target':body['target_email']='other@example.com'
    elif changed=='client':options['identifier']=str(c.other_client)
    elif changed=='version':body['expected_row_version']=2
    else:body['previous_user_id']=str(c.foreign_user)
    response=await post(p,body,**options)
    assert response.status_code==409 and response.json()['error']['code']=='IDEMPOTENCY_KEY_REUSED'
    assert snapshot(p)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('key',[None,'',' ','x'*256])
async def test_key_is_mandatory_and_validated_before_workflow(operator_http,key):
    p=operator_http;before=snapshot(p)
    response=await post(p,key=key)
    assert response.status_code==422 and snapshot(p)==before
    p.workflow.request_email_link.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('options',[{'identifier':'not-a-uuid'},{'params':{'tenant_id':'private-tenant'}},
    {'params':{'expires_at':'private-expiry'}},{'headers':[('Idempotency-Key','another-key')]}])
async def test_path_query_and_duplicate_key_are_rejected(operator_http,options):
    p=operator_http;before=snapshot(p)
    response=await post(p,**options)
    assert response.status_code==422 and snapshot(p)==before
    assert 'private-tenant' not in response.text and 'private-expiry' not in response.text
    p.workflow.request_email_link.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'expected_row_version':True},{'expected_row_version':'1'},
    {'expected_row_version':0},{'expected_row_version':2**63},{'target_user_id':None},{'target_user_id':'private-user'},
    {'tenant_id':'private-tenant'},{'user_id':'private-user'},{'linked_user_id':'private-user'},
    {'confirmed':True},{'token':'private-token'},{'expires_at':'private-expiry'},{'lifetime_hours':1},
    {'previous_user_id':'private-user'},{'recipient_context':'private-context'}])
async def test_body_rejects_unapproved_fields_types_and_server_context(operator_http,changes):
    p,c=operator_http,operator_http.c;before=snapshot(p)
    response=await post(p,{'target_email':'person@example.com','expected_row_version':1,**changes})
    assert response.status_code==422 and snapshot(p)==before
    assert all(value not in response.text for value in ('private-user','private-tenant','private-token','private-expiry','private-context'))
    p.workflow.request_email_link.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['anonymous','inactive','permission','wrong_tenant','wrong_user','project_only',
    'suite','module','rate','identity','membership','revoked','missing_target'])
async def test_gates_and_fresh_authority_prevent_pending_request_and_invalidation(operator_http,case):
    p,c=operator_http,operator_http.c;before=snapshot(p);expected=403
    if case=='anonymous':p.app.dependency_overrides.pop(get_current_actor);expected=401
    elif case=='inactive':p.app.dependency_overrides[get_current_actor]=supplied(replace(c.actor,status='blocked'))
    elif case in ('permission','wrong_tenant','wrong_user'):
        changes={'permission':{'permissions':()},'wrong_tenant':{'tenant_id':uuid4()},'wrong_user':{'user_id':uuid4()}}[case]
        p.app.dependency_overrides[get_construction_access_context]=supplied(replace(c.context,**changes))
    elif case=='project_only':
        p.app.dependency_overrides[get_construction_access_context]=supplied(ConstructionAccessContext(c.operator,c.tenant,
            ('PROJECT_MANAGER',),('construction.projects.edit',),(ConstructionGrantContext('PROJECT_MANAGER','project',uuid4()),)))
    elif case=='suite':p.suite.require_enabled.side_effect=SuiteDisabledError()
    elif case=='module':p.module.require_enabled.side_effect=ModuleDisabledError()
    elif case=='rate':p.rate.ensure_authenticated_user_allowed.side_effect=ApiRequestRateLimitExceededError();expected=429
    elif case=='identity':p.issue_identity.require_active_actor.side_effect=ApiIdentityAccessError('private identity failure')
    elif case=='membership':c.access.has_active_platform_membership.return_value=False
    elif case=='revoked':c.grant.status='revoked'
    else:p.issue_identity.require_existing_user.side_effect=ConstructionClientValidationError('private missing target');expected=422
    response=await post(p)
    assert response.status_code==expected,response.text
    assert snapshot(p)==before and 'data' not in response.json()
    assert 'private identity failure' not in response.text and 'private missing target' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['missing','foreign','archived','stale','self_target'])
async def test_unavailable_client_and_conflicting_base_cannot_issue(operator_http,case):
    p,c=operator_http,operator_http.c;options={};expected=404
    body={'target_email':'person@example.com','expected_row_version':1}
    if case=='missing':options['identifier']=str(uuid4())
    elif case=='foreign':options['identifier']=str(c.foreign_client)
    elif case=='archived':
        c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(deleted_at=c.now));c.sync.commit()
    elif case=='stale':body['expected_row_version']=2;expected=409
    else:p.email_reader.find_verified_user_id.return_value=c.operator;expected=422
    before=snapshot(p)
    response=await post(p,body,**options)
    assert response.status_code==expected and snapshot(p)==before
    if case=='stale':assert response.json()['error']['code']=='ROW_VERSION_CONFLICT'


@pytest.mark.asyncio
async def test_replacement_requires_matching_previous_identity_and_leaves_old_link_until_confirm(operator_http):
    p,c=operator_http,operator_http.c
    c.sync.execute(update(ConstructionClient.__table__).where(ConstructionClient.id==c.client).values(linked_user_id=c.foreign_user))
    c.sync.commit()
    before=snapshot(p)
    assert (await post(p)).status_code==422 and snapshot(p)==before
    body={'target_email':'person@example.com','expected_row_version':1,'previous_user_id':str(uuid4())}
    wrong=await post(p,body)
    assert wrong.status_code==409 and wrong.json()['error']['code']=='ROW_VERSION_CONFLICT' and snapshot(p)==before
    body['previous_user_id']=str(c.foreign_user)
    response=await post(p,body)
    assert response.status_code==201 and client_state(c)==(c.foreign_user,1)
    assert p.workflow.request_email_link.await_count==3
    assert c.audit_rows[-1].payload['identity_link_action']=='request_replacement'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['audit','complete','commit','bad_completion','final_authority'])
async def test_mandatory_failures_restore_preexisting_request_and_all_other_rows(operator_http,failure):
    p,c=operator_http,operator_http.c;before=snapshot(p)
    if failure=='audit':c.events.create_event.side_effect=RuntimeError('private audit failure')
    elif failure=='complete':p.flags.fail_complete=True
    elif failure=='commit':p.flags.fail_commit=True
    elif failure=='bad_completion':p.flags.corrupt_complete=True
    else:
        original=p.ledger.complete_record
        async def complete(**kw):
            saved=await original(**kw);c.access.has_active_platform_membership.return_value=False;return saved
        p.ledger.complete_record=complete
    response=await post(p)
    assert response.status_code==(403 if failure=='final_authority' else 500),response.text
    assert snapshot(p)==before and 'data' not in response.json() and 'private audit failure' not in response.text
    assert c.sync.get(Request,c.request_id).invalidated_at is None


@pytest.mark.asyncio
async def test_pending_reservation_returns_processing_without_invalidating_old_token(operator_http):
    p=operator_http
    p.runtime=routes.ClientLinkRequestRuntime(SimpleNamespace(request_email_link=AsyncMock(side_effect=
        ConstructionClientLinkIdempotencyInProgressError('private reservation'))),p.context_codec)
    before=snapshot(p)
    response=await post(p)
    assert response.status_code==409 and response.json()['error']['code']=='idempotency_in_progress'
    assert snapshot(p)==before and 'private reservation' not in response.text


@pytest.mark.asyncio
async def test_new_http_context_roundtrips_through_existing_target_confirmation_route(operator_http):
    p,c=operator_http,operator_http.c
    issued=recipient_material(p, (await post(p)).json()['data'])
    p.app.dependency_overrides[get_authenticated_platform_principal]=supplied(ApiPlatformPrincipal(c.target))
    p.app.dependency_overrides[confirmation_routes.get_confirmation_crypto_settings]=supplied(SimpleNamespace(encryption_key=p.key))
    p.app.dependency_overrides[confirmation_routes.get_confirmation_scoped_session]=supplied(c.session)
    p.app.dependency_overrides[confirmation_routes.get_confirmation_workflow]=supplied(p.confirm_workflow)
    if not c.sync.in_transaction():c.sync.begin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=p.app,raise_app_exceptions=False),base_url='http://testserver') as client:
        response=await client.post('/api/v1/construction/client-user-link-requests/confirm',
            json={'recipient_context':issued['recipient_context'],'token':issued['token']},headers={'Idempotency-Key':'confirm-new-http-request'})
    assert response.status_code==200,response.text
    assert response.json()['data']['linked_user_id']==str(c.target) and client_state(c)==(c.target,2)
    assert all(call.kwargs['user_id']==c.operator for call in c.access.has_active_platform_membership.await_args_list)


def test_factory_requires_shared_server_key_and_separate_canonical_platform_session(operator_http,monkeypatch):
    p,c=operator_http,operator_http.c
    platform=SimpleNamespace(execute=AsyncMock(),no_autoflush=nullcontext())
    runtime=routes.get_client_link_request_runtime('key',SimpleNamespace(encryption_key=p.key),c.session,platform)
    assert runtime.workflow.service.identity.session is platform
    assert runtime.workflow.session is runtime.workflow.service.requests.session is runtime.workflow.idempotency.repository.session is c.session
    assert runtime.workflow.service.confirmation_lifetime==timedelta(hours=24)
    with pytest.raises(ApiHttpError) as invalid:
        routes.get_client_link_request_runtime('key',SimpleNamespace(encryption_key='private-key'),c.session,platform)
    assert invalid.value.status_code==503 and 'private-key' not in str(invalid.value)
    def unavailable():raise ApiConfigurationError('private encryption key config')
    monkeypatch.setattr(routes.ApiIdempotencySettings,'from_env',unavailable)
    with pytest.raises(ApiHttpError) as missing:routes.get_client_link_request_settings()
    assert missing.value.status_code==503 and 'private encryption' not in str(missing.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('key',[None,'valid-key'])
async def test_actual_runtime_dependency_rejects_missing_key_before_configuration(operator_http,monkeypatch,key):
    p=operator_http
    p.app.dependency_overrides.pop(routes.get_client_link_request_runtime)
    calls=[]
    def unavailable():
        calls.append('settings')
        raise ApiConfigurationError('private server configuration')
    monkeypatch.setattr(routes.ApiIdempotencySettings,'from_env',unavailable)
    before=snapshot(p)
    response=await post(p,key=key)
    assert response.status_code==(422 if key is None else 503),response.text
    assert calls==([] if key is None else ['settings'])
    assert snapshot(p)==before and 'private server configuration' not in response.text
    p.workflow.request_email_link.assert_not_called()


def test_openapi_requires_key_and_exposes_only_approved_request_fields(operator_http):
    p=operator_http
    path='/api/v1/construction/clients/{id}/user-link/requests'
    schema=p.app.openapi();operation=schema['paths'][path]['post']
    assert '201' in operation['responses'] and '409' in operation['responses'] and '422' in operation['responses']
    assert any(x['in']=='header' and x['name']=='Idempotency-Key' and x['required'] is True for x in operation['parameters'])
    body=schema['components']['schemas']['ClientLinkRequestBody']
    assert set(body['properties'])=={'target_email','expected_row_version','previous_user_id'}
    assert set(body['required'])=={'target_email','expected_row_version'} and body['additionalProperties'] is False
    registered=[r for item,r in walk_routes(p.app) if item==path and 'POST' in getattr(r,'methods',())]
    assert len(registered)==1 and registered[0].endpoint is routes.issue_client_link_request


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['untyped','audit','tenant','target','client','initiator','version','client_version','expiry','token'])
async def test_bad_workflow_receipts_never_disclose_material(operator_http,bad):
    p,c=operator_http,operator_http.c
    state=replace(c.request,expires_at=c.request.issued_at+timedelta(hours=24))
    material=ConstructionClientLinkIssuanceResult(state,TOKEN,uuid4())
    if bad=='untyped':material={'private':'private material'}
    elif bad=='audit':material=replace(material,audit_id=None)
    elif bad=='token':material=replace(material,token='private-token')
    else:
        changes={'tenant':{'tenant_id':uuid4()},'target':{'target_user_id':c.operator},
            'client':{'client_id':uuid4()},'initiator':{'initiated_by_user_id':uuid4()},
            'version':{'row_version':2},'client_version':{'expected_client_row_version':2},
            'expiry':{'expires_at':state.expires_at-timedelta(hours=1)}}[bad]
        material=replace(material,request=replace(state,**changes))
    p.runtime=routes.ClientLinkRequestRuntime(SimpleNamespace(request_email_link=AsyncMock(return_value=material)),p.context_codec)
    before=snapshot(p)
    response=await post(p)
    assert response.status_code==500 and 'data' not in response.json() and snapshot(p)==before
    assert TOKEN not in response.text and 'private material' not in response.text and 'private-token' not in response.text


def test_replayable_context_is_stable_encrypted_token_bound_and_legacy_compatible(operator_http):
    p,c=operator_http,operator_http.c
    material=ConstructionClientLinkIssuanceResult(c.request,TOKEN,uuid4())
    first=p.context_codec.issue_replayable_for_request(material=material)
    second=Codec(encryption_key=p.key).issue_replayable_for_request(material=material)
    assert first==second and first.recipient_context.startswith(p.context_codec.REPLAYABLE_PREFIX)
    assert TOKEN not in first.recipient_context and c.request.token_hash not in first.recipient_context
    assert str(c.tenant) not in first.recipient_context and str(c.target) not in first.recipient_context
    assert TOKEN not in repr(first) and first.recipient_context not in repr(first)
    legacy=p.context_codec.issue_for_request(material=material)
    assert p.context_codec.resolve(recipient_context=first.recipient_context,token=TOKEN)==p.context_codec.resolve(recipient_context=legacy.recipient_context,token=TOKEN)


@pytest.mark.parametrize('case',['tamper','wrong_key','wrong_token','noncanonical','empty','purpose','other_request'])
def test_replayable_context_rejects_tampering_without_reading_identity_or_db(operator_http,case):
    p,c=operator_http,operator_http.c
    material=ConstructionClientLinkIssuanceResult(c.request,TOKEN,uuid4())
    first=p.context_codec.issue_replayable_for_request(material=material)
    context,token,codec=first.recipient_context,TOKEN,p.context_codec
    if case=='tamper':
        pos=len(codec.REPLAYABLE_PREFIX)+12;context=context[:pos]+('A' if context[pos]!='A' else 'B')+context[pos+1:]
    elif case=='wrong_key':codec=Codec(encryption_key=Fernet.generate_key().decode())
    elif case=='wrong_token':token='ccl1_'+'A'*43
    elif case=='noncanonical':context+='='
    elif case=='empty':context=codec.REPLAYABLE_PREFIX
    elif case=='purpose':context='other_'+context
    else:
        different=replace(material,request=replace(c.request,request_id=uuid4()))
        assert codec.issue_replayable_for_request(material=different).recipient_context!=context
        return
    with pytest.raises(Denied):codec.resolve(recipient_context=context,token=token)


@pytest.mark.asyncio
async def test_exact_email_missing_verified_user_returns_explicit_404(operator_http):
    p = operator_http
    p.email_reader.find_verified_user_id.return_value = None
    before = snapshot(p), jobs(p)
    response = await post(p)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "PLATFORM_USER_NOT_FOUND"
    assert (snapshot(p), jobs(p)) == before
    p.queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_is_normalized_and_caller_target_uuid_is_rejected(operator_http):
    p = operator_http
    bad = await post(p, {
        "target_email": "person@example.com",
        "target_user_id": str(p.c.target),
        "expected_row_version": 1,
    })
    assert bad.status_code == 422
    p.queue.enqueue.assert_not_awaited()
    good = await post(p, {
        "target_email": " PERSON@EXAMPLE.COM ",
        "expected_row_version": 1,
    })
    assert good.status_code == 201, good.text
    p.email_reader.find_verified_user_id.assert_awaited_once_with(
        email="person@example.com",
    )
    assert "token" not in good.json()["data"]
    assert "recipient_context" not in good.json()["data"]

# client_link_email_http_v1
