# CON-C013: activity audit correlation

Authority: rev.12 CON-C013 client-card history, plus customer clarification
13.10.2026 21:01 in new clarification.txt (History / audit).

Create/complete/cancel use the existing linked-row locks (lead, client, project)
and retain their tenant/identity/availability validation. Validated rows are
kept locally and passed to construction_audit_client_details together with
the validated created/updated activity. No new queries or lock order changes.
Direct client, converted lead and linked project may provide a canonical client.
All known references must agree for a singular related_client_id. Unknown or
conflicting ownership yields no guessed singular marker, using the prepared
helper contract. Existing multiple-link business semantics are unchanged.

The UUID string is captured in the same transaction as the domain operation
and its one audit event. Complete/cancel retain result_changed and timestamp/
status changes, without logging private result/comment. Scope, permissions,
optimistic versions, lifecycle, transaction ownership and HTTP routes remain
unchanged. No idempotency request hash, namespace, response or record changes.
Replay returns the existing snapshot without adding/backfilling audit.

Existing events retain operation-time attribution even after lead conversion
or project relinking. No historical updates, SQL schema/policy changes,
new permissions, Supabase, Storage or real email operations are performed.
Tests cover all seven link combinations for all three operations, ambiguity,
invalid evidence, fixed locks, replay and real local SQL commit/rollback.

This does not complete CON-C013. Restricted Construction audit reading under
RLS, legacy history rules, transitive planned activities and card aggregation
remain outstanding. Planned activities inclusion uses the separately approved
OR/deduplication contract; singular audit attribution does not replace it.
Next: CON_C013_PLANNED_ACTIVITIES_CLIENT_READ.
