# CON-C013: client history evidence preparation

Authority: rev.12 CON-C013 client-card history, customer clarification
13.10.2026 21:01 in new clarification.txt (History / audit; event_logs reading).

The pure contract fixes 12 exact existing event/entity pairs for client,
lead, project and activity events. Invitation, grant, area, Admin, HR,
financial, security and unrelated platform events are outside this card
allowlist. Prefix matching is not a substitute. Existing audit write registry
and permissions are unchanged. Tenant is a trusted UUID; event/entity UUIDs
and the exact domain pair must match before evidence is considered.

New related_client_id is an operation-time canonical UUID string. A present
invalid marker never falls back to another relationship. Intrinsic client
identity and explicit audit changes cannot contradict it. No current mutable
project/lead relationship or contact/name/address match can replace a marker.
Archived entities do not erase event-time evidence.

Marker-less event-data proofs in this increment are:
- client event: its intrinsic entity UUID;
- project update: explicit client_id change's new UUID, never the old value;
- lead conversion: explicit converted_client_id change plus the recorded
  nonterminal -> won transition, with consistent created_client_id if present.
Unknown, malformed, duplicated or contradictory evidence is excluded instead
of reconstructed by assumption. Creation/status events with no client evidence
are not assigned to today's client. Removed client linkage is not attributed
to the old client. Old event data and idempotency records are not rewritten.

This is pure preparation. It does not query event_logs, authorize a caller,
produce a public history response, create a repository or install SELECT/RLS.
Entity-based legacy proofs are not implemented here and remain for repository
review where immutability/event-time evidence can be established. This helper
does not substitute for required tenant/scope/membership checks or restricted
RLS. No admin.logs.read, BYPASSRLS, UPDATE or DELETE privilege is introduced.

Next repository must filter only approved pairs and client evidence in SQL
before bounded pagination, independently validate returned evidence, materialize
only a domain history projection and recheck current authorization. RLS must
limit actual construction_api SELECT to current tenant/Construction events;
inspect metadata before applying any SQL change. No general Admin Audit API.

Tests cover the exact registry, current logger compatibility, marker precedence,
legacy conversion/reassignment proof, later relinking, archived history, foreign
tenant, wrong pair, unrelated domains and malformed payloads without mutation.
No Supabase, Storage or real email operations are run by this installer.
CON-C013 and Stage 1A are not complete.
Next: CON_C013_CLIENT_HISTORY_REPOSITORY.
