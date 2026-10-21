# Platform: nullable primary tenant compatibility

Source: customer clarification of 2026-10-07 21:52 approving nullable
public.users.tenant_id for remove from organization.

User.tenant_id is already Optional/nullable in ORM. The latest Supabase
preflight still reports NOT NULL. This increment prepares local compatibility
before the separate user-run Platform migration.

ApiIdentityService.require_actor requires UUID user_id and selected tenant_id
before looking up the user. A NULL primary tenant must never make a NULL request
tenant count as membership through None == None.

An active global user without a primary tenant can use an explicitly selected
tenant only with active Platform membership there. Role/permission/scope reads
remain bound to that selected tenant. No fallback tenant is selected and no
primary tenant, roles or user status are changed by this read.

Local tests cover rejected NULL/invalid request identifiers, secondary
membership, revoked/missing membership and foreign-role isolation, including
real UserRepository role queries against SQLite.

This increment does not alter Supabase, implement remove from organization,
revoke grants or define project membership lifecycle.

Broader local API foundation checking also reports an existing
test_api_layer_has_no_direct_business_access failure for repository imports
in API dependencies/routes. Those files are outside this increment and unchanged.
Two subprocess import checks additionally failed in the Windows probe because
the child interpreter did not inherit the appended workspace dependencies.
The targeted auth/access/membership regression passes; full API regression
is not claimed.
