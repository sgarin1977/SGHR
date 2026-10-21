# CON-C011: activity repository and derived next_action

Authority: rev.12 section 5.1 and Stage 1A CON-C011; clarifications.txt activity
fields, types, planned/completed/cancelled lifecycle, UTC/IANA deadline and
next_action contract; related-list sorting due_at ASC/id ASC and limits 20/100.

ConstructionActivityRepository creates planned/version-1 records using the
existing links and schedule value objects. It reconstructs those objects to
revalidate frozen values, uses server UTC timestamps, flushes once and neither
commits nor rolls back. Creator/tenant are required; responsible user and comment
are nullable. Terminal/result fields start null. No permission or membership is
inferred by the repository: the forthcoming service must verify every linked
resource, responsible-user membership and effective access, and write audit.

Reads select by tenant and exclude deleted rows, with identity refresh and
autoflush disabled. The for-update read requires an existing transaction. Terminal
activities remain readable as history. Parent availability is a service check.

get_next_action_for_lead selects the first non-deleted planned activity by
due_at ASC/id ASC. It does not restrict deadlines to the future; overdue actions
are still unfinished actions. Completed, cancelled and archived actions are
excluded. No next_action column or duplicate lead state is written.

list_planned_activities uses the same ordering, optional conjunctive parent/user
filters and a tenant-bound ascending cursor. Default/max page limits are 20/100;
one additional row is fetched so the caller can determine a next page. This is
repository behavior, not an additional public HTTP contract.

Local tests exercise actual SQLite creation/rollback, tenant filters, derived
next action, ties, overdue deadlines, terminal/archive exclusion and stale ORM
refresh, and compile PostgreSQL read/lock queries. No native PostgreSQL/RLS or
deployed HTTP behavior is claimed. Existing application files are preserved;
the physical Supabase activity table is still not created in this increment.
Next: CON_C011_ACTIVITY_CREATE_SERVICE.
