# CON-C013: cursor transport preparation

Authority: clarifications.txt CON-C013, independent lists, default 20/max 100,
cursor pagination preference and approved sorting. This helper prepares existing
internal service positions for transport. It registers no route and changes no
existing HTTP response, query parameter, permission, database schema or policy.
The final public card response layout remains pending clarification.

encode_client_card_cursor/decode_client_card_cursor accept an explicit list name
(projects/leads/activities/history) and trusted server tenant/user/client UUIDs.
They operate on the existing four service cursor classes. Tokens contain only
format version, section, context UUIDs, last timestamp and resource/event UUID.
Timestamp normalization is UTC with exact microseconds. No page offset, data
lookup, entity body, audit payload, contact, authentication token or key is used.

The helper uses canonical URL-safe base64 JSON. Decode is bounded at 1024 input
characters and rejects invalid base64, alternate unused bits, duplicate keys,
unexpected/missing fields, unsupported version, wrong types, noncanonical UUIDs,
naive/invalid/noncanonical timestamps and wrong section or context. Encode also
revalidates cursor type/context and tampered position fields. None represents
the initial position or absence of another page; an empty token is invalid.
Errors expose a fixed generic ConstructionClientValidationError, without input.

These are pagination positions, not signed authentication credentials. A caller
can edit a canonical same-context position; this must never widen accessible
rows. Current membership/grants, tenant/client constraints, ordering, page bound
and scope checks remain mandatory in the real read services on every request.
No encryption/signing/idempotency configuration is introduced for pagination.

Tests cover all four cursor types, timezone/microsecond round trips, context
binding, malformed/oversized/ambiguous encodings, wrong cursor classes, tampered
objects and rejection of unsafe input shapes. Existing card/read regressions
remain selected. No native database, Storage, email or old audit record is read
or written by this preparation. CON-C013 remains incomplete.
