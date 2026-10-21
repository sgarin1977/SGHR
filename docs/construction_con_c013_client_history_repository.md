# CON-C013: restricted client history repository

Authority: rev.12 CON-C013 and customer clarification 13.10.2026 21:01,
new clarification.txt (History / audit and event_logs reading).

The query restricts tenant, exact allowed event/entity/operation triples and
operation-time client evidence before ORDER BY and LIMIT. It uses the prepared
history contract: related_client_id, intrinsic client UUID or explicit audit
changes proving project reassignment/lead conversion. Unknown, malformed,
contradictory or duplicated evidence is excluded in SQL, not silently discarded
after pagination. Current mutable entity links are not joined or guessed.
Present invalid markers cannot fall back to another client association.
Parent entity archival is not used as an event filter; old event evidence stays.

JSONB type/shape checks guard array expansion and object subtraction with CASE
so malformed input cannot become an unsafe UUID cast or scalar-array failure.
Only eight fields are selected: id, tenant_id, user_id, event_type, entity_type,
entity_id, payload, created_at. No generic administrative log read is introduced.
Returned records are internal evidence, not an HTTP/public history projection.
Each result is independently checked against the pure contract; unexpected,
duplicated, oversized or incorrectly ordered results fail closed.

Ordering is descending created_at/id, limit 1..100 plus one lookahead. Cursor
binds tenant/client and exact event position. EventLog currently uses naive UTC
timestamps in ORM. Cursor normalizes naive UTC and aware timestamps to UTC;
the seek predicate uses numeric epoch with microsecond precision instead of
assuming the physical timestamp type or session timezone for a datetime bind.
No event model/schema/timestamps are rewritten. No locks, flush or transaction
commit/rollback occurs; read authorization belongs to the later service.

PostgreSQL operators reference:
https://www.postgresql.org/docs/current/functions-json.html
Timestamp EXTRACT/EPOCH reference:
https://www.postgresql.org/docs/current/functions-datetime.html

Local tests compile PostgreSQL queries and execute the production relational
predicate/order/limit through a SQLite JSONB-operation surrogate, comparing
results to the independent prepared contract. They cover marker and legacy
proof, malformed/non-object JSON, unrelated domains, cross-tenant exclusion,
filter-before-limit, ties, microsecond cursor seek and no autoflush. The SQL
surrogate is not native PostgreSQL/RLS acceptance; native planning/behavior
checks remain after read privileges have been inspected and configured.

Scope: repository increment only. The service/public projection, entity-based
legacy evidence review, limited event_logs SELECT/RLS, card aggregation and
HTTP integration remain outstanding. No Supabase, Storage or real email call,
new permission, mutation of history/idempotency, or SQL privilege change runs
in this installer. It does not complete CON-C013 or Stage 1A.
Next: CON_C013_CLIENT_HISTORY_READ_SERVICE.
