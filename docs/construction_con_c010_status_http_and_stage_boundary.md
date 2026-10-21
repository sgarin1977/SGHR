# CON-C010: approved lead status HTTP route and stage boundary clarification

Authority: customer's explicit confirmation supplied in this task after the
CON-C011 repository result (142 passed), together with rev.12 section 5.1 and
retained Stage 1A permission, lifecycle, version and atomic audit contracts.

PATCH /api/v1/construction/crm/leads/{id}/status is now explicitly approved.
Request has status and required expected_row_version. The endpoint uses shared
authentication, Construction suite/core entitlements and projects.edit permission.
Trusted actor context supplies tenant/user; request body cannot override them.
It calls the existing status workflow, with one transaction for status and audit.

Success uses HTTP 200 and the common data/meta/request_id envelope. Data contains
id, tenant_id, status and row_version. A real change advances version once and
writes one audit; matching current status is no-op without advance or audit.
Stale version uses 409 ROW_VERSION_CONFLICT. Existing forbidden/not_found/conflict/
validation_error/internal_error codes cover other failures without private detail.
Idempotency-Key is not required. The endpoint does not load encryption settings.

The route is registered once and described in OpenAPI. Local ASGI tests exercise
the real service/workflow with offline collaborators, strict request/response,
auth/entitlement/scope denials, audit/commit failures, no-op and version conflict.
Existing workflow SQL tests cover local transaction rollback. Deployed auth,
native PostgreSQL and concurrency are not claimed by this installer.

## Superseding Stage 1 / Measurement boundary

The customer confirms that Measurement/Area endpoints in rev.12 section 22 are
the general Release contract and are accepted with the full Measurement roadmap
stage. Stage 1 does not require minimal Measurement domain or surrogate entities.
Stage 1 remains foundation/CRM/access. This applies to the listed measurement
list/create/header/edit/item/submit/verify operations, area creation via measurement,
area PATCH/duplicate/archive and measurement area reorder.

Previously recorded missing Measurement context and duplicate/measurement HTTP
implementation therefore no longer block Stage 1 acceptance. Earlier documents
that list those dependencies as Stage 1 blockers are superseded on this point.
Already implemented area code is preserved; its existence does not imply full
Measurement acceptance. No measurement tables are introduced or removed here.

Remaining Stage 1 acceptance must be assessed against the corrected scope and
actual implementation/verification; Stage 1 and Stage 1A are not marked accepted
by this route installation. The retained 26 test seed rows, including four users,
still require targeted cleanup after the checks that use them.

Next implementation: CON_C011_ACTIVITY_CREATE_SERVICE.
