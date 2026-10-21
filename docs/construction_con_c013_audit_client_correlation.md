# CON-C013: operation-time audit client correlation preparation

Authority: rev.12 client card CON-C013 and full business descriptions;
new clarification.txt, customer confirmation 13.10.2026 21:01 (History/audit).
The existing EventLog payload gains related_client_id for new related events
when the client is known at operation time. No new model, permission, audit
table or historical data rewrite is introduced.

This increment provides construction_audit_client_details as a pure helper.
It supports construction_client, construction_lead, construction_project and
construction_activity. Inputs are canonical persisted resources from the same
business transaction after the operation's normal tenant/authorization checks.
The caller must retain the normal locks/transaction consistency; this helper
does not open sessions, authorize access or acquire locks on its own.

Client uses its own UUID; project uses client_id; lead uses converted_client_id.
Activity gathers its direct client and the clients of every linked lead/project.
Every linked activity resource must be supplied and match both tenant and UUID.
Malformed, missing or mismatched evidence raises ConstructionAuditContractError.
No contact/name/address matching or caller-provided related_client_id is used.

Only one distinct known candidate produces a UUID string payload fragment.
No known client means no marker. Conflicting clients also produce no singular
marker: choosing a client arbitrarily would violate the explicit requirement
for unambiguous history attribution. This does not reject the activity's existing
business links or change the separately approved OR rule for planned activities.
The helper must not be used as the planned-activity list predicate.

Archived resources retain correlation; this is not the non-deleted list filter.
The returned fragment is a snapshot with no retained ORM references. It can be
passed as details to the existing ConstructionAuditLogger, which copies details
into a new event payload. Later link changes must not alter already written
events. Tests exercise the existing logger and verify this snapshot behavior.

Scope: preparation only. Production mutators are not wired in this increment,
so their new events do not yet automatically acquire the marker. Integrate
client/project, lead and activity operations in subsequent guarded increments.
Legacy-history proof, restricted event_logs RLS SELECT, history repository,
planned-activity transitive read, client-card aggregation and HTTP remain.
There is no retroactive backfill, guessed historical reconstruction or change
to idempotency snapshots. This increment does not complete CON-C013/Stage 1A.

Local checks: canonical relationships, indirect activity links, missing/foreign
evidence, archived rows, ambiguous links, unknown pre-conversion client and
snapshot stability. No native RLS, deployed auth or live database writes claimed.
Next: CON_C013_CLIENT_PROJECT_AUDIT_CORRELATION.
