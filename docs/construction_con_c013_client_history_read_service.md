# CON-C013: domain client history read service

Authority: rev.12 CON-C013 and customer clarification 13.10.2026 21:01,
new clarification.txt, History/audit and restricted event_logs reading.

The service requires the existing construction.projects.read permission,
tenant CRM scope, current active Platform membership and effective grants.
Project-only scope, creator/responsible identity, inactive actors or mismatched
tenant/user context cannot read a client card. No admin.logs.read is required
or granted. All repositories share one caller-owned active transaction and RLS
context. No flush/lock/commit/rollback or write operation is performed.

History includes archived entities. Repository gains a dedicated two-column
client identity SELECT by tenant/id without deleted_at filtering, for this
history read path only. Existing active client CRUD/list/get behavior remains.
No lookup of today's related project/lead/activity can erase or reattribute a
past event. Historical tenant/client attribution is independently revalidated
against the prepared event-time evidence contract. Entity-based legacy proofs
beyond the earlier event-data subset remain outstanding for separate review.

Membership/grants are refreshed before query and after query, with client
identity checked twice. Revocation, membership loss or a disappearing/foreign
client prevents any publication, including an empty page/cursor. Project events
also retain project scope checks. Cursor binds user/tenant/client, timestamp
and UUID; descending order, duplicates, lookahead and page bounds are checked.

Public records have eight explicit fields: id, tenant_id, user_id, event_type,
entity_type, entity_id, payload, created_at. Existing EventLog naive UTC/aware
timestamps are normalized to UTC for the response. payload contains operation,
reason and only current approved domain change fields. Unknown root details or
unknown change fields are omitted; non-scalar/nonfinite values in known fields
fail closed. Conversion UUID details and terminal activity result_changed are
validated per event type. Private result/comment data are not added. No raw ORM,
trace_id, headers, platform scopes or arbitrary payload details are published.
The response is detached from source records. Legacy markers are not invented.

No HTTP route, permission code, SQL migration, RLS policy or audit/idempotency
mutation is added. Local tests exercise access changes during awaited reads,
all domain event types, projection safety, archived history, cursor behavior
and repository/service SQL integration using the existing local SQL surrogate.
This does not claim native PostgreSQL/RLS or deployed HTTP acceptance.
Next: inspect actual event_logs ACL/policies read-only before restricted SELECT.
CON-C013 and Stage 1A remain incomplete; aggregation/HTTP and acceptance remain.
