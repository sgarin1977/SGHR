# CON-C008 role options read contract

GET /api/v1/construction/team/role-options?scope_type=tenant|project&scope_id=UUID

This backend increment supplies role choices for access-grant management.
It does not assign roles, create or revoke grants, edit their lifetime,
change membership or introduce individual permission switches.

## Authorization and policy

Requires an authenticated active actor, construction Suite/core module
and construction.access.manage. The existing scope-delegation guard
checks management access to the requested tenant or project.

Roles and permissions come from the approved fixed Construction policy.
Tenant scope allows OWNER and ADMIN only. ADMIN cannot delegate OWNER.
Project options are filtered by the actor's permissions in that project;
rights granted in another project do not expand the choices.

Project references must identify an existing, non-deleted project in the
actor tenant. The repository performs one read without FOR UPDATE.
Tenant options do not require a project query.

Document assignment options are denied until the approved Document Center
resource validator exists. The model's document scope remains unchanged.

## Response

Standard success envelope. data contains:

- scope_type
- scope_id
- roles: array of role and its sorted permissions
- permissions_read_only: true

The permissions describe a proposed role in the selected scope.
Existing employee permissions remain available through the Team read API.
The role-options response does not authorize a subsequent mutation:
that operation must recheck current actor rights and resource state.

No Idempotency-Key, expected_row_version, business audit or database write
is required for this GET. Grant mutation concurrency remains pending the
customer clarification; no exception is introduced here.

## Verification boundaries

Local tests use the actual policy, role-options service and project
repository with mocked SQL sessions and ASGI authentication fixtures.
They verify tenant roles, scoped delegation, resource existence,
foreign-resource rejection and immutable permission output.

The installer performs no Supabase, Storage or real email operations.
The complete role-assignment UI, mutations and deployed E2E remain
outside this increment.
