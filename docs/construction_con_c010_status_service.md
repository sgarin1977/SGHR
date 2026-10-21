# CON-C010: lead status service

Authority: rev.12 CON-C010 and the approved nine-state CRM lifecycle;
clarifications.txt, CRM permission family and shared mutation/version/audit
contracts; new clarification.txt retains the full lifecycle and shared permission
engine. CRM measurement_completed does not establish a Measurement domain fact.

ConstructionLeadStatusService uses construction.projects.edit through the shared
engine. It requires an active actor, current Platform membership and effective
tenant scope for this tenant-level lead operation, independently of responsible
user identity. It rechecks membership/grants before and after the refreshed
tenant-scoped FOR UPDATE read. Project/document-only access does not provide
tenant-wide CRM access. The clock is server-side and UTC.

The immutable command carries next_status and expected_row_version. Matching
current status is a successful no-op after permission and version checks,
including terminal states: no UPDATE, version increment or audit. A stale version
still conflicts. Real transitions use the approved validator, then conditional
repository UPDATE. Failed conditional state/version matching uses the shared
ROW_VERSION_CONFLICT. No terminal reopening or skipped/reverse transition exists.

Exactly one construction_lead_status_changed audit records the old/new status.
All other lead fields, conversion links and related entities are preserved.
Ordinary negotiation -> won does not create conversion targets. No separate
measurement operation is performed by a CRM status change.

Service/repositories/audit share one session. Service neither commits nor rolls
back; the caller must roll back every failure, including an audit failure after
the status UPDATE. A transactional workflow is the next increment.

Tests cover current permission/membership loss, row locks, conflicts, no-op,
approved transitions and terminal restrictions, exact audit, unchanged fields,
sanitized errors and actual SQLite commit/rollback of status and audit. Native
PostgreSQL, deployed HTTP/auth and concurrent transactions are not claimed here.
No schema, HTTP routes, Supabase, Storage or real email operation is added.
Next: CON_C010_LEAD_STATUS_WORKFLOW.
