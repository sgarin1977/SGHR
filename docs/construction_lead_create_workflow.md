# CON-C009: atomic lead creation workflow

Authority: rev.12 CRM lead creation and the specified Stage 1 HTTP surface; Stage 1A clarification requiring Idempotency-Key for create lead; 07.10 source/work-type clarification, and 08.10 common authorization decisions. Lead domain belongs to Stage 1A and provides a dependency of the requested Stage 1 HTTP closure.

The workflow uses the shared ApiIdempotencyService/Repository and existing response encryption configuration. It requires an active caller-opened transaction and shared session for the lead, access checks, mandatory event_logs audit, and idempotency reservation. On success it commits once; on failure it rolls back. No new permission codes, registries, project members, automatic grants or emails are introduced.

A key is mandatory. Reservation scope is tenant + user principal + construction.lead.create + key hash. The request hash covers supplied command values before input normalization. The same key and command returns the original creation snapshot without another lead or audit; a changed command raises ApiIdempotencyKeyReusedError for the HTTP adapter to map to 409 IDEMPOTENCY_KEY_REUSED. Processing reservations return an in-progress conflict. Retention and atomic reservation mechanics remain those of the shared platform service.

Responses contain explicit public creation fields only. The encrypted stored wrapper binds the snapshot to its tenant, user, operation, key hash, and request hash; these internal binding fields are not returned publicly or added to domain audit. Fresh creation uses status new, version 1, and aware UTC timestamps. Replay preserves the original snapshot after later business edits, checks current platform membership and effective shared creation permission again, and requires that the created lead still exists in its tenant and is not soft-deleted. Lost access or unavailable lead prevents disclosure of the cached contact details.

source remains an extensible normalized code. work_type_description preserves the initial description. Canonical work-type classification remains deferred until the actual MASTER/ORGANIZATION catalogs are introduced under their approved contract.

Local verification covers the real shared idempotency service and Fernet codec, changed-body conflicts, current-access denial, processing state, tampered/swapped snapshot rejection, and shared-session wiring. SQLite tests prove joint commit/rollback of lead, test audit and test reservation, including failures after audit/response writes. They do not prove production PostgreSQL concurrency or RLS.

This installer creates response/workflow/test/document files only. It performs no Supabase, Storage, or real email operations. Physical lead schema is not created. HTTP wiring and production database acceptance remain subsequent work.
