# CON-C004 Team Read API

## Status
Backend read contract implemented and verified by local tests.
This is not acceptance of the complete Team UI or live E2E.
Latest combined local regression: 512 passed.

## Endpoint
GET /api/v1/construction/team/members

Optional query parameter: project_id (UUID).
The server obtains tenant_id and user_id from authenticated actor context.
No client-supplied tenant_id is trusted for authorization.
GET /api/v1/construction/team/access-grants remains unchanged.

## Access
Requires an active actor, enabled construction suite and construction.core
module, plus construction.team.manage in the requested scope.
Project-scoped management does not permit tenant-wide or other-project reads.
Denied requests do not read team grants or identity accounts.

## Selection
Only active, nonexpired grants in the current tenant are selected.
With project_id: matching project grants plus applicable tenant grants.
Without project_id: current tenant grants across scopes.
One response row per user_id; missing identity never removes the user.

## Response
Standard success envelope: data (array), meta (object), request_id (string).
Each member: user_id, display_name, email, roles, grants, scoped_permissions.
Each grant: id, role, scope_type, scope_id, expires_at.
Each scoped_permissions entry: grant_id, role, scope_type, scope_id, permissions.
UUIDs are JSON strings; expires_at is a timestamp string or null.
Roles summarize the selected grants; they are not tenant-wide authorization.
Permissions are read-only, derived per grant from the fixed role policy.
No individual permission switches or personal permission grants are added.

## Identity
Source: existing platform user_accounts; no copied Construction identity.
Use exactly one eligible nonblank account display name; otherwise null.
Use exactly one distinct platform-confirmed email; otherwise null.
No ranking by account order, id, login time, or creation time.
Current proof: successful platform email-auth event linked to the same user
and a used OTP/magic-link challenge matching that email account.
A populated email or account source alone is not confirmation.
Grants use Construction session; identity uses a separate Platform session.
No additional identity/auth-table rights are granted to construction_api.

## Errors And Verification
401: authentication required or invalid authentication.
403: permission_required, forbidden, or MODULE_DISABLED as applicable.
422: invalid project_id, using the existing validation_error envelope.
Local coverage includes aggregation, scope isolation, identity ambiguity,
email proof SQL, separate-session wiring, HTTP responses and access denials.
Live populated cross-tenant E2E and complete Team UI remain unverified.
