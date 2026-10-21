# CON-C011 activity domain rules

Stage 1A, first implementation increment. Source: rev.12 section 5.1
and final clarifications for construction_activities.

## Implemented rules

Activity types: call, message, meeting, site_visit, follow_up, task, other.
The ordinary lifecycle is planned -> completed or cancelled.
Completed and cancelled are terminal. An idempotent replay belongs to the
future workflow and does not perform another status transition.

ConstructionActivityLinks validates UUID references and requires at least
one of lead_id, client_id or project_id. Multiple references are allowed.

ConstructionActivitySchedule stores an absolute aware deadline normalized
to UTC, together with a validated IANA display timezone. UTC input does
not need to have the display timezone's offset. Explicit DST fold instants
remain distinct. Naive deadlines are rejected rather than guessed.

These are immutable value objects and validation functions, not parallel
tables or placeholder lead/activity records.

## Remaining CON-C011 work

The final activity storage contract includes id, tenant_id, type, status,
lead_id, client_id, project_id, due_at, timezone, responsible_user_id,
comment, result, completed_at, cancelled_at, created_by_user_id,
created_at, updated_at, deleted_at and row_version.

Before persistence, services must validate linked resource existence,
tenant ownership, caller permissions and responsible-user rules.
The value objects validate reference shape only.

Creation, completion and cancellation require Idempotency-Key.
Changes to existing mutable records require expected_row_version.
Business changes and mandatory audit must share one transaction.

next_action is derived from the closest non-deleted planned activity
by due_at. It is not a mutable column on lead. Its query/read model is
not implemented in this increment.

## Dependencies and verification

The approved final table must retain its lead relation. construction_leads
storage is pending clarification of source, work_type and lifecycle.
No reduced activity schema or temporary lead table is introduced.

Tests exercise only the domain rules. This increment creates no database
table, migration, endpoint, audit event or real provider operation.
It does not close the complete CON-C011 feature.
