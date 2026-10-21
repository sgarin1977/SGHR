# CON-C013: legacy history from an immutable client-only activity

Authority: customer clarification 13.10.2026 21:01 in new clarification.txt:
marker-less events may enter history only where the original client relationship
is unambiguous from the entity or available audit evidence. No old event, payload
or idempotency record is modified, and no related_client_id is synthesized.

This increment integrates one specific entity-based proof through repository,
history service and card composition: an activity directly linked to a client,
with no lead_id or project_id. Current approved activity operations create links
once and only update terminal fields afterwards; the deployed activity ACL was
reported to allow updates only to those terminal fields. Before native acceptance,
revalidate that link/context/created_at columns remain immutable for construction_api.
Marker-bearing events keep their original event-data attribution and do not join
current activities for reinterpretation. Mutable project/client and post-conversion
lead/client references are not used to reconstruct other legacy events.

For marker-less activity events, the repository LEFT JOIN requires exact activity
ID, same tenant, direct client, no lead/project links, and compares event timestamp
against entity creation time. Only events at/after creation qualify. Explicit
link-field changes in an event cannot use the immutable-link fallback. Normal
event/entity/operation/changes-shape filters still apply. Archive/deleted state
does not erase the activity's past relationship. Mixed, missing, foreign, future
or ambiguous entity links remain excluded. WHERE proof filters precede cursor,
ordering and LIMIT; no post-limit filtering, guessed parent link or N+1 lookup.

Four private activity identity fields accompany the existing eight event_logs
columns. The frozen evidence object is independently checked against event tenant,
entity ID and timestamps in the pure contract, repository validation and history
service. The history page carries only proofs for returned marker-less activity
events, outside its public items. The card composer validates proof type, event
binding, completeness and attribution; duplicate/unused/foreign proofs fail closed.
The public event still has eight fields and the original marker-less payload.
Private page evidence must never be serialized by an HTTP adapter; serialize only
explicit public event items and transport cursors. Card HTTP is still pending.

All reads retain membership/grants, tenant/client/scope checks and shared caller
transaction/no_autoflush behavior. No locks, flush, commit, rollback or audit write
is added. Existing event SELECT policy and eight-column grants are unchanged; the
new join uses the existing activity read permission under native tenant RLS.
Local SQL surrogate tests verify archive preservation, negative evidence, pre-limit
filtering and cursor behavior; real services verify proof handoff through the card.
They do not claim native PostgreSQL/RLS, production concurrency or HTTP acceptance.

Remaining: native planning/ACL revalidation for the new join, native history RLS
behavior, HTTP format confirmation/integration and remaining provable legacy cases
outside this client-only activity increment. CON-C013/Stage 1A remain incomplete.
