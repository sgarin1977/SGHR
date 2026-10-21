<!-- lead-description-contract-2026-10-07 -->
> Implementation update: the original lead text is now work_type_description.
> The source whitelist and former work_type identity assumptions below are
> historical; canonical MASTER/ORGANIZATION classification remains deferred
> under the 07.10.2026 allowance. See construction_lead_work_type_description.md
> for the current local contract and physical-schema checkpoint.

<!-- lead-contract-2026-10-07 -->
> Contract update: the 07.10.2026 customer clarification supersedes the source
> whitelist and final free-text work_type assumptions below. Source is now a
> required extensible code; see construction_lead_source_2026_10_07.md.
> The work_type_description / later canonical classification implementation is
> a separate pending increment. The approved lead lifecycle and grant-history
> contracts remain in force. This document retains the earlier implementation
> history and must not be used to restore the superseded source/work_type rules.

# CON-C009 / CON-C010: tenant-scoped lead repository

Basis: rev.12 section 5.1, the approved lead fields and final 2026-10-06
source/work_type/lifecycle contract. Ordering/page limits follow the related-list
contract: updated_at DESC, id DESC; default limit 20 and maximum visible limit 100.

ConstructionLeadRepository creates a lead from ConstructionLeadInput and a trusted
tenant UUID/aware server timestamp. New records have status=new, row_version=1,
no conversion references and no deleted_at. Creation adds and flushes once.
It performs no membership lookup, audit, idempotency reservation, commit or rollback.

get_active_lead and get_active_lead_for_update require tenant/id predicates and
exclude soft-deleted rows. The locking variant requires an existing transaction
and uses FOR UPDATE with populate_existing. All read/list statements disable
autoflush and refresh returned ORM state; intended changes must be flushed before
a later re-read, and services must snapshot old values before writes.

list_active_leads includes non-deleted won/lost records. Optional source/status,
responsible user and converted client/project filters are conjunctive with tenant.
ConstructionLeadCursor holds tenant, UTC updated_at and lead UUID. A foreign-tenant
cursor or invalid limit/reference is rejected before SQL. Keyset comparison uses
(updated_at,id) strictly below the cursor with descending order.

The repository returns at most limit+1 raw rows. The read service must return at
most limit visible items and build the next cursor from the last retained item,
not from the lookahead row. No public cursor encoding or snapshot-consistent
pagination guarantee across concurrent updates is introduced here.

Authorization, narrower resource scope, active responsible-user membership,
audit and transaction ownership belong to services/workflows. Tenant predicates
alone do not authorize a caller. next_action remains derived from future activities;
no activity data or private identity profiles are fabricated.

Tests compile PostgreSQL statements and run SQLite in-memory operations with a
small async adapter. They cover create rollback, tenant/deleted isolation, ordering,
keyset tie-breaks, terminal records and stale ORM refresh without autoflush.
Actual PostgreSQL locks/RLS and physical Supabase installation remain pending.
Next: lead creation service with current permissions, assignment validation and
mandatory audit, followed by the required idempotent workflow and HTTP.
