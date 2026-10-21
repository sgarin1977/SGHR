# CON-C013: lead audit correlation

Authority: rev.12 CON-C013 client-card history and customer confirmation
in new clarification.txt, 13.10.2026 21:01 (History / audit).

New lead creation, status change and conversion audits use the prepared
tenant-checked correlation helper. Creation has no converted client, so no
related_client_id is guessed from name, email, phone or another resource.
Status events use the persisted updated row's converted_client_id if known.
Conversion uses the validated completed lead's converted_client_id and stores
its canonical UUID string in the single existing conversion event. Existing
created_client_id/created_project_id details and all changes remain intact.
Nested creation events remain collected, with no extra persisted child audit.

The marker is a snapshot at operation time; later relationship changes cannot
change previously inserted events. Existing events, encrypted replay records,
request hashes and HTTP responses are not rewritten. No-op, conflict, terminal
state, authorization and replay behavior remain unchanged. No reopening or new
lead lifecycle operation is introduced. Transaction ownership is unchanged.

Local tests cover unknown references, canonical correlation, all three
conversion modes, frozen event attribution, no-op/stale paths, encrypted replay,
and real local SQL commit/rollback with legacy events preserved.
No Supabase, Storage or real email operations run in the installer.

This is a partial CON-C013 increment. Activity correlation, restricted audit
reading under RLS, transitive planned activities and complete card aggregation
remain outstanding. Stable Platform idempotency key deployment is still pending
after the prior native rollback probe used an ephemeral key.
Next: CON_C013_ACTIVITY_AUDIT_CORRELATION.
