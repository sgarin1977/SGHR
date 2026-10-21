# CON-C006 access grant read API

GET /api/v1/construction/team/members/{user_id}/access-grants

This Stage 1A increment exposes the existing
ConstructionAccessGrantService.list_access operation. It does not
change grants, membership, roles, expiry or storage schema.

## Authorization

Authenticated active actor, enabled construction Suite and construction.core,
and construction.access.manage from the existing fixed role policy.
The service checks management access in the requested scope before
reading grant rows. construction.team.manage alone is insufficient.

tenant_id comes from the authenticated actor. The path user_id is the
target user. Query tenant_id does not change the authorization context.

## Read contract

The response uses the existing success envelope and returns an array of
active, nonexpired grants for the target user in the actor tenant.

Optional project_id selects direct project-scoped assignments through the
existing service filter. Applicable tenant grants and effective inherited
project access remain available through the Team read model.

Each item contains id, user_id, role, scope_type, scope_id, status,
expires_at and granted_at. Internal metadata is not projected.
An empty list is valid. No audit event or business mutation is created.
No Idempotency-Key or expected_row_version is required for this GET.

401: authentication failure.
403: permission_required, forbidden or MODULE_DISABLED.
422: invalid user_id or project_id.
500: repository failure or invalid returned grant scope, with a generic
error response and no private details.

## Boundaries

Existing grants may contain a document scope in the read model.
This endpoint does not issue document grants; their creation remains
blocked until the approved Document Center resource validator exists.

Grant mutation concurrency is pending the customer clarification on
row_version. This increment introduces no exception to that requirement.

Tests use mocked SQL sessions and ASGI HTTP clients. Live RLS, deployed
authentication and the complete access-management UI are not verified
by these local tests.
