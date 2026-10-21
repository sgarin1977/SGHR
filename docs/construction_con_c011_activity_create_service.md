# CON-C011: activity creation service

Authority: rev.12 section 5.1 and CON-C011, clarifications.txt activity model,
shared projects.read/create/edit family and tenant/project/document authorization,
atomic audit and UTC/IANA contracts. Newly added new clarification.txt confirms
Measurement deferral and CON-C010 HTTP; it does not change the activity contract.

The service requires an active verified actor and caller-opened transaction.
An immutable creation command normalizes approved activity type and comment,
revalidates existing links/schedule values, and accepts nullable responsible user.
Tenant, creator, initial planned/version 1 state and timestamps are server-owned.
Completed/cancelled/result fields cannot be submitted during creation.

Authorization uses construction.projects.create in the shared engine. A lead or
client link requires tenant-level CRM scope; a project link requires access to
that project. Project-only grants cannot expand to arbitrary tenant CRM data.
Every selected resource must exist, be non-deleted and belong to the actor tenant.
Locks follow lead -> client -> project order, with autoflush disabled. Multiple
links remain supported without inventing conversion or parent reassignment.

The responsible user, when supplied, must be an active Platform member of the
tenant; no role or grant is generated implicitly. Membership/effective grants and
responsible membership are rechecked around awaited resource locks before insert.
The created row is verified against all server/command fields before writing one
construction_activity_created event. Its payload follows the shared create audit
shape and does not duplicate comment/contact data.

All repositories/service/audit share one session. The service never commits or
rolls back; the caller must roll back every failure, including mandatory audit.
The next increment provides creation idempotency and workflow transaction ownership,
as create activity requires Idempotency-Key. No HTTP route is introduced here.

Local tests cover links, current permission/scope/membership, responsible user,
clock/server fields, exact audit, sanitized failures and SQLite commit/rollback
of actual activity/audit writes. They do not establish native PostgreSQL/RLS,
concurrency, external email or deployed authentication. The physical Supabase
activity table is not created by this installer.
Next: CON_C011_ACTIVITY_CREATE_WORKFLOW.
