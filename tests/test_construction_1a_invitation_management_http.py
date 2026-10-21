from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

import api.routes.construction_invitation_management as routes
from api.auth import get_current_actor
from api.routes.construction import get_construction_access_context, get_construction_scoped_session
from services.api_idempotency import ApiIdempotencyKeyReusedError
from services.construction_foundation import ConstructionRowVersionConflictError
from services.construction_invitation_workflows import ConstructionInvitationIdempotencyInProgressError
from services.construction_invitations import ConstructionInvitationTransitionError, ConstructionInvitationValidationError
from services.construction_permissions import (
    CONSTRUCTION_ROLE_PERMISSIONS, ConstructionAccessContext, ConstructionGrantContext,
    ConstructionPermissionDeniedError, ConstructionScopeDeniedError,
)
from services.module_access import ModuleDisabledError
from test_construction_1a_invitation_acceptance_http import http_case

@pytest.fixture
def management_case(http_case):
    case = http_case
    case.invitation_id = uuid4()
    case.access = ConstructionAccessContext(
        user_id=case.actor.user_id, tenant_id=case.actor.tenant_id, roles=("OWNER",),
        permissions=tuple(CONSTRUCTION_ROLE_PERMISSIONS["OWNER"]),
        grants=(ConstructionGrantContext("OWNER", "tenant", case.actor.tenant_id),),
    )
    deadline = datetime.now(timezone.utc) + timedelta(days=7)
    case.create_body = {
        "email": "person@example.com", "role": "ADMIN",
        "scopes": [{"scope_type": "tenant", "scope_id": str(case.actor.tenant_id)}],
        "expires_at": deadline.isoformat(), "grant_expires_at": None,
    }
    response = {
        "id": str(case.invitation_id), "email": "person@example.com", "role": "ADMIN",
        "status": "pending", "scopes": case.create_body["scopes"],
        "expires_at": deadline.isoformat(), "grant_expires_at": None, "row_version": 6,
    }
    case.management = SimpleNamespace(
        create_invitation=AsyncMock(return_value=dict(response)),
        resend_invitation=AsyncMock(return_value=dict(response)),
        revoke_invitation=AsyncMock(return_value={**response, "status": "revoked", "row_version": 5}),
    )
    case.app.dependency_overrides[get_construction_scoped_session] = lambda: object()
    case.app.dependency_overrides[get_construction_access_context] = lambda: case.access
    case.app.dependency_overrides[routes.get_inviter_workflow] = lambda: case.management
    return case

async def post(case, operation, *, anonymous=False, changes=None, headers=None, invalid_id=False):
    if not anonymous:
        case.app.dependency_overrides[get_current_actor] = lambda: case.actor
    if operation == "create":
        path, body = "/api/v1/construction/invitations", dict(case.create_body)
    else:
        identifier = "invalid" if invalid_id else str(case.invitation_id)
        path = "/api/v1/construction/invitations/" + identifier + "/" + operation
        body = {"expected_row_version": 4}
    body.update(changes or {})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=case.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.post(
            path + "?tenant_id=" + str(uuid4()), json=body,
            headers={"Idempotency-Key": "management-key", "X-Request-ID": "management-http"} if headers is None else headers,
        )

@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "resend", "revoke"])
async def test_management_dispatch_uses_actor_tenant_and_existing_workflow(management_case, operation):
    case = management_case
    response = await post(case, operation)
    assert response.status_code == (201 if operation == "create" else 200), response.text
    expected = dict(actor=case.actor, access_context=case.access, idempotency_key="management-key", trace_id="management-http")
    if operation == "create":
        expected.update(
            email="person@example.com", role="ADMIN",
            scopes=[("tenant", case.actor.tenant_id)],
            expires_at=datetime.fromisoformat(case.create_body["expires_at"]), grant_expires_at=None,
        )
    else:
        expected.update(invitation_id=case.invitation_id, expected_row_version=4)
        if operation == "resend":
            expected["expires_at"] = None
    getattr(case.management, operation + "_invitation").assert_awaited_once_with(**expected)
    assert response.json()["data"]["row_version"] == (5 if operation == "revoke" else 6)
    assert response.json()["request_id"] == "management-http"
    case.suite.require_enabled.assert_awaited_once_with(tenant_id=case.actor.tenant_id, suite_code="construction")
    case.module.require_enabled.assert_awaited_once_with(tenant_id=case.actor.tenant_id, module_code="construction.core")

@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "resend", "revoke"])
@pytest.mark.parametrize("failure", ["anonymous", "inactive", "no_permission", "foreign_context", "module", "missing_key", "blank_key", "tenant_body"])
async def test_management_http_denies_before_workflow(management_case, operation, failure):
    case = management_case
    changes, headers = {}, None
    expected = 403
    if failure == "anonymous":
        expected = 401
    elif failure == "inactive":
        case.actor.status = "suspended"
    elif failure in ("no_permission", "foreign_context"):
        case.access = ConstructionAccessContext(
            user_id=case.actor.user_id,
            tenant_id=uuid4() if failure == "foreign_context" else case.actor.tenant_id,
            roles=("OBSERVER",), permissions=(), grants=(),
        )
    elif failure == "module":
        case.module.require_enabled.side_effect = ModuleDisabledError()
    elif failure == "missing_key":
        headers, expected = {}, 422
    elif failure == "blank_key":
        headers, expected = {"Idempotency-Key": "   "}, 422
    else:
        changes, expected = {"tenant_id": str(uuid4())}, 422
    response = await post(case, operation, anonymous=failure == "anonymous", changes=changes, headers=headers)
    assert response.status_code == expected, response.text
    getattr(case.management, operation + "_invitation").assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resend", "revoke"])
@pytest.mark.parametrize("version", [True, "4", 0, -1])
async def test_management_http_requires_strict_positive_version(management_case, operation, version):
    response = await post(management_case, operation, changes={"expected_row_version": version})
    assert response.status_code == 422, response.text
    getattr(management_case.management, operation + "_invitation").assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"scopes": []},
    {"role": "UNREGISTERED"},
    {"scopes": [{"scope_type": "document", "scope_id": str(uuid4())}]},
    {"scopes": [{"scope_type": "tenant", "scope_id": str(uuid4()), "token": "private-secret"}]},
    {"expires_at": "2026-10-06T12:00:00"},
    {"grant_expires_at": 123},
])
async def test_management_create_rejects_invalid_contract(management_case, changes):
    response = await post(management_case, "create", changes=changes)
    assert response.status_code == 422, response.text
    assert "private-secret" not in response.text
    management_case.management.create_invitation.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("error,status,code", [
    (ConstructionRowVersionConflictError(), 409, "ROW_VERSION_CONFLICT"),
    (ConstructionInvitationTransitionError(), 409, "INVITATION_STATE_CONFLICT"),
    (ApiIdempotencyKeyReusedError(), 409, "IDEMPOTENCY_KEY_REUSED"),
    (ConstructionInvitationIdempotencyInProgressError(), 409, "idempotency_in_progress"),
    (ConstructionPermissionDeniedError(), 403, "forbidden"),
    (ConstructionScopeDeniedError(), 403, "forbidden"),
    (ConstructionInvitationValidationError(), 422, "validation_error"),
    (RuntimeError("private-secret"), 500, "internal_error"),
])
async def test_management_preserves_domain_errors_without_secrets(management_case, error, status, code):
    management_case.management.resend_invitation.side_effect = error
    response = await post(management_case, "resend")
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert "private-secret" not in response.text

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["token", "nested", "id", "version", "status"])
async def test_management_rejects_invalid_public_workflow_output(management_case, failure):
    case = management_case
    saved = dict(case.management.resend_invitation.return_value)
    if failure == "token":
        saved["token"] = "private-secret"
    elif failure == "nested":
        saved["scopes"] = [{**saved["scopes"][0], "token_hash": "private-secret"}]
    elif failure == "id":
        saved["id"] = str(uuid4())
    elif failure == "version":
        saved["row_version"] = 4
    else:
        saved["status"] = "accepted"
    case.management.resend_invitation.return_value = saved
    response = await post(case, "resend")
    assert response.status_code == 500
    assert "private-secret" not in response.text

def test_management_factory_shares_constructor_session_and_configured_crypto(monkeypatch):
    calls = []
    expected, session = object(), object()
    def build(**kwargs):
        calls.append(kwargs)
        return expected
    monkeypatch.setattr(routes, "build_construction_invitation_workflow", build)
    assert routes.get_inviter_workflow(session=session, settings=SimpleNamespace(encryption_key="configured-key")) is expected
    assert calls == [{
        "session": session, "idempotency_encryption_key": "configured-key",
        "delivery_encryption_key": "configured-key",
    }]

def test_management_openapi_requires_idempotency_key(management_case):
    paths = management_case.app.openapi()["paths"]
    for path in (
        "/api/v1/construction/invitations",
        "/api/v1/construction/invitations/{invitation_id}/resend",
        "/api/v1/construction/invitations/{invitation_id}/revoke",
    ):
        header = next(item for item in paths[path]["post"]["parameters"] if item["name"] == "Idempotency-Key")
        assert header["required"] is True and header["in"] == "header"
