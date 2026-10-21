from construction_rate_limit_fixture import with_local_construction_rate_limit
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

import api.routes.construction_lead_status as routes
from api.app import create_app
from api.auth import get_current_actor
from api.dependencies import get_tenant_module_access_service, get_tenant_suite_access_service
from api.errors import ApiHttpError
from api.routes.construction import get_construction_access_context
from services.construction_permissions import ConstructionAccessContext, ConstructionGrantContext
from services.module_access import ModuleDisabledError, SuiteDisabledError
from test_construction_1a_lead_status_service import status_case
from test_construction_1a_lead_status_workflow import workflow_case


@pytest.fixture
def status_http(workflow_case):
    c=workflow_case
    c.foreign=uuid4()
    c.suite=SimpleNamespace(require_enabled=AsyncMock())
    c.module=SimpleNamespace(require_enabled=AsyncMock())
    c.app=with_local_construction_rate_limit(create_app())
    c.app.dependency_overrides[get_current_actor]=lambda: c.actor
    c.app.dependency_overrides[get_construction_access_context]=lambda: c.context
    c.app.dependency_overrides[get_tenant_suite_access_service]=lambda: c.suite
    c.app.dependency_overrides[get_tenant_module_access_service]=lambda: c.module
    c.app.dependency_overrides[routes.get_lead_status_workflow]=lambda: c.workflow
    return c


async def patch(c, *, data=None, identifier=None, key=None):
    headers={"X-Request-ID":"lead-status-http","X-Tenant-ID":str(c.foreign)}
    if key is not None: headers["Idempotency-Key"]=key
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=c.app,raise_app_exceptions=False),
        base_url="http://testserver") as client:
        return await client.patch("/api/v1/construction/crm/leads/"+(identifier or str(c.lead.id))+"/status",
            json={"status":"measurement_scheduled","expected_row_version":4} if data is None else data,
            headers=headers,params={"tenant_id":str(c.foreign)})


def no_mutation(c):
    c.repo.transition_status.assert_not_called()
    c.events.create_event.assert_not_called()
    c.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("key",[None,"unneeded-key"])
async def test_approved_patch_uses_real_workflow_trusted_tenant_one_audit_and_no_required_key(status_http,key):
    c=status_http
    response=await patch(c,key=key)
    assert response.status_code==200,response.text
    assert response.json()==dict(data=dict(id=str(c.lead.id),tenant_id=str(c.tenant),
        status="measurement_scheduled",row_version=5),meta={},request_id="lead-status-http")
    assert response.headers["X-Request-ID"]=="lead-status-http"
    assert c.order==["update","audit","commit"]
    c.suite.require_enabled.assert_awaited_once_with(tenant_id=c.tenant,suite_code="construction")
    c.module.require_enabled.assert_awaited_once_with(tenant_id=c.tenant,module_code="construction.core")
    assert c.events.create_event.await_args.kwargs["trace_id"]=="lead-status-http"
    assert not any(secret in response.text for secret in ("private@example.com","Private comment",
        "key_hash","ciphertext","unneeded-key","changed"))


@pytest.mark.asyncio
@pytest.mark.parametrize("status",["contacted","won","lost"])
async def test_same_status_success_has_no_update_audit_or_version_increment(status_http,status):
    c=status_http
    c.lead.status=status
    response=await patch(c,data=dict(status=status,expected_row_version=4))
    assert response.status_code==200 and response.json()["data"]["row_version"]==4
    c.repo.transition_status.assert_not_called(); c.events.create_event.assert_not_called()
    assert c.order==["commit"]


@pytest.mark.asyncio
async def test_stale_no_op_is_still_version_conflict(status_http):
    c=status_http
    response=await patch(c,data=dict(status="contacted",expected_row_version=3))
    assert response.status_code==409 and response.json()["error"]["code"]=="ROW_VERSION_CONFLICT"
    no_mutation(c)
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["anonymous","inactive","suite","module","permission","wrong_tenant","wrong_user",
    "project_scope","document_scope","illegal_tenant"])
async def test_auth_entitlements_role_and_scope_denial_precedes_mutation(status_http,failure):
    c=status_http
    if failure=="anonymous":
        def anonymous(): raise ApiHttpError(status_code=401,code="authentication_required",message="Authentication required.")
        c.app.dependency_overrides[get_current_actor]=anonymous
    elif failure=="inactive": c.actor.status="blocked"
    elif failure in ("suite","module"):
        (c.suite if failure=="suite" else c.module).require_enabled.side_effect=SuiteDisabledError() if failure=="suite" else ModuleDisabledError()
    else:
        role="PROJECT_MANAGER" if failure=="illegal_tenant" else "OWNER"
        scope="project" if failure=="project_scope" else "document" if failure=="document_scope" else "tenant"
        c.context=ConstructionAccessContext(uuid4() if failure=="wrong_user" else c.user,
            c.foreign if failure=="wrong_tenant" else c.tenant,(role,),
            () if failure=="permission" else ("construction.projects.edit",),
            (ConstructionGrantContext(role,scope,c.tenant),))
    response=await patch(c)
    assert response.status_code==(401 if failure=="anonymous" else 403),response.text
    if failure in ("suite","module"): assert response.json()["error"]["code"]=="MODULE_DISABLED"
    no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("data",[{},dict(status="contacted"),dict(expected_row_version=4),
    dict(status="qualified",expected_row_version=4),dict(status="proposal",expected_row_version=4),
    dict(status=" contacted ",expected_row_version=4),dict(status=None,expected_row_version=4),
    dict(status=True,expected_row_version=4),dict(status="contacted",expected_row_version=True),
    dict(status="contacted",expected_row_version="4"),dict(status="contacted",expected_row_version=4.0),
    dict(status="contacted",expected_row_version=0),dict(status="contacted",expected_row_version=2**63),
    dict(status="contacted",expected_row_version=4,tenant_id=str(uuid4())),
    dict(status="contacted",expected_row_version=4,responsible_user_id=str(uuid4())),
    dict(status="contacted",expected_row_version=4,row_version=5),
    dict(status="contacted",expected_row_version=4,next_status="won")])
async def test_only_two_required_approved_body_fields_are_accepted(status_http,data):
    c=status_http
    response=await patch(c,data=data)
    assert response.status_code==422 and response.json()["error"]["code"]=="validation_error"
    no_mutation(c)


@pytest.mark.asyncio
async def test_invalid_uuid_path_is_sanitized_validation_error(status_http):
    c=status_http
    response=await patch(c,identifier="private-invalid-id")
    assert response.status_code==422 and "private-invalid-id" not in response.text
    no_mutation(c)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["version","conditional","terminal","reverse","skipped","missing","foreign","deleted",
    "membership","revoked","audit","commit"])
async def test_domain_errors_use_existing_machine_codes_and_rollback(status_http,failure):
    c=status_http
    data=dict(status="measurement_scheduled",expected_row_version=4)
    code,status="internal_error",500
    if failure=="version": c.lead.row_version=5; code,status="ROW_VERSION_CONFLICT",409
    elif failure=="conditional":
        c.repo.transition_status.side_effect=None; c.repo.transition_status.return_value=None
        code,status="ROW_VERSION_CONFLICT",409
    elif failure in ("terminal","reverse","skipped"):
        if failure=="terminal": c.lead.status="won"
        else: data["status"]="new" if failure=="reverse" else "negotiation"
        code,status="conflict",409
    elif failure in ("missing","foreign","deleted"):
        if failure=="missing": c.repo.get_active_lead_for_update.return_value=None
        elif failure=="foreign": c.lead.tenant_id=c.foreign
        else: c.lead.deleted_at=c.now
        code,status="not_found",404
    elif failure in ("membership","revoked"):
        if failure=="membership": c.access.has_active_platform_membership.return_value=False
        else: c.grant.status="revoked"
        code,status="forbidden",403
    elif failure=="audit": c.events.create_event.side_effect=RuntimeError("private audit diagnostic")
    else: c.session.commit.side_effect=RuntimeError("private commit diagnostic")
    response=await patch(c,data=data)
    assert response.status_code==status,response.text
    assert response.json()["error"]["code"]==code and "private" not in response.text
    c.session.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["extra","tenant","id","status","version","bool_version","uuid_object","noncanonical"])
async def test_route_rejects_foreign_or_malformed_workflow_output(status_http,failure):
    c=status_http
    result=dict(id=str(c.lead.id),tenant_id=str(c.tenant),status="measurement_scheduled",row_version=5)
    if failure=="extra": result["secret"]="private-result"
    elif failure in ("tenant","id"): result["tenant_id" if failure=="tenant" else "id"]=str(uuid4())
    elif failure=="status": result["status"]="lost"
    elif failure=="version": result["row_version"]=8
    elif failure=="bool_version": result["row_version"]=True
    elif failure=="uuid_object": result["id"]=c.lead.id
    else: result["id"]=c.lead.id.hex
    fake=SimpleNamespace(transition_status=AsyncMock(return_value=result))
    c.app.dependency_overrides[routes.get_lead_status_workflow]=lambda: fake
    response=await patch(c)
    assert response.status_code==500 and "private-result" not in response.text


def walk_routes(router,prefix=""):
    for route in router.routes:
        original=getattr(route,"original_router",None)
        if original is not None:
            context=getattr(route,"include_context",None)
            yield from walk_routes(original,prefix+getattr(context,"prefix",""))
        elif hasattr(route,"path"): yield prefix+route.path,route


def test_one_approved_route_and_openapi_contract_without_required_idempotency_header(status_http):
    c=status_http
    path="/api/v1/construction/crm/leads/{id}/status"
    matches=[route for item,route in walk_routes(c.app) if item==path and "PATCH" in getattr(route,"methods",())]
    assert len(matches)==1 and matches[0].endpoint is routes.change_lead_status
    assert routes.status_permission.construction_permission_code=="construction.projects.edit"
    schema=c.app.openapi()
    assert set(schema["paths"][path])=={"patch"}
    operation=schema["paths"][path]["patch"]
    assert not any(item["name"]=="Idempotency-Key" for item in operation.get("parameters",[]))
    assert {"200","401","403","404","409","422","500"}<=set(operation["responses"])
    model=schema["components"]["schemas"]["LeadStatusRequest"]
    assert set(model["properties"])==set(model["required"])=={"status","expected_row_version"}
    assert model["additionalProperties"] is False
    assert set(model["properties"]["status"]["enum"])=={
        "new","contacted","measurement_scheduled","measurement_completed","estimate_preparation",
        "proposal_sent","negotiation","won","lost"}


def test_factory_uses_shared_scoped_session_without_idempotency_settings(status_http):
    c=status_http
    c.session.execute=AsyncMock(); c.session.add=Mock(); c.session.flush=AsyncMock()
    workflow=routes.get_lead_status_workflow(session=c.session)
    assert workflow.session is workflow.service.session is workflow.service.repository.session is c.session
    assert routes.get_lead_status_workflow.__defaults__[0].dependency is routes.get_construction_scoped_session


def test_http_adapter_has_no_direct_repository_or_transaction_calls():
    tree=ast.parse(Path(routes.__file__).read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        modules=[item.name for item in node.names] if isinstance(node,ast.Import) else [node.module or ""] if isinstance(node,ast.ImportFrom) else []
        assert not any(module.startswith(("database.repositories","aiogram","handlers")) for module in modules)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
            assert node.func.attr not in {"commit","rollback","flush"}
