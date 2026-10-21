# CON-C013: client/project audit correlation integration

Authority: rev.12 CON-C013 and client-card business description; customer
confirmation new clarification.txt, 13.10.2026 21:01, History/audit.

New client created/updated/archived events include payload.related_client_id
as the client's canonical UUID string. Project created/updated/status_changed
events snapshot the persisted project's client_id after the successful domain
write. An unlinked project produces no marker. Archived clients retain identity.
The prepared helper validates resource UUID and tenant against the trusted actor.
No matching by contacts, names or current client-card query is introduced.

The marker is materialized immediately before the existing audit insertion in
the same transaction. Moving a project to another client changes the marker of
the new operation's event; prior events keep their original client membership.
Existing operation/changes/reason and transaction ownership remain intact.
Project no-op/stale/denied paths retain their existing audit behavior.
This increment does not change the legacy client's update/no-op/version contract.

No SQL table/policy/grant, permission code, HTTP route, business lifecycle or
idempotency namespace/hash/snapshot format changes. Old events and stored replay
snapshots are neither read for repair nor rewritten. Replays create no new audit.
Lead conversion's collector validates these markers against the selected/created
client while retaining exactly one persisted conversion audit, with no extra
persisted client/project substep events. The lead-converted event marker itself
is a later increment. Lead/status and activity event integration, restricted history read,
transitive planned activities and full card aggregation remain outstanding.

Tests verify direct operation calls, caller/legacy transactions, archived identity,
unlinked projects, client reassignment/history stability, malformed resource
failure, no extra no-op/stale audit, and actual local SQL commit/rollback with audit.
Existing tests are updated only where the approved payload gained the marker or
minimal mocks lacked fields now read from persisted resources.
No live Supabase, Storage or email calls run in this installer. A stable Platform
API_IDEMPOTENCY_ENCRYPTION_KEY still needs deployment configuration; the prior
native rollback test used an ephemeral key when that setting was absent.
Next: CON_C013_LEAD_AUDIT_CORRELATION.
