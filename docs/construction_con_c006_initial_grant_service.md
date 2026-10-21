# CON-C006/C008: initial grant issuance service

Sources: rev.12 section 4.3.8 and CON-C006; final clarification of 2026-10-06.

ConstructionAccessIssuanceService.grant_access prepares a new historical grant
and construction_access_grant_created audit in the same Construction session.
It does not commit; the owning workflow commits the complete operation.

The service uses the existing Construction role/scope policy and canonical
has_active_platform_membership repository check. Platform identity, user_roles
and membership are not created, restored, deactivated or otherwise written.

Authority is resolved from current grants, including scope-specific delegation.
It is checked before target lookups and refreshed after awaited project and
membership checks. Project scope requires an existing own-tenant nondeleted
project locked through get_active_project_for_update. Document issuance stays
unavailable before the Document Center resource validator.

Server time is read after awaited checks. Expiry must be an aware future
datetime and is normalized to UTC. No expiry is represented by null.
The new row records the actor as granted_by_user_id and the server issuance time.
Existing historical grants are not edited or revoked by initial issuance.

The existing effective-grant temporal exclusion constraint controls duplicates.
Only its structured SQLSTATE 23P01 diagnostic is mapped to ACCESS_GRANT_CONFLICT.
No grant row_version, new permissions, physical tables or migrations are added.

Tests use local in-memory SQLite persistence and the actual permission resolver
and audit logger with controlled repository dependencies. They verify prepared
grant/audit state and rollback, not live PostgreSQL concurrency, RLS or deployment.

This step implements the service only. Next: owning issuance workflow with
transaction completion and optional Platform idempotency, then HTTP integration.
CON-C006/C008 and Stage 1A are not declared complete by this test result.
