# CON-C011: approved activity HTTP endpoints

Authority: rev.12 CON-C011 plus customer confirmation in new clarification.txt,
13.10.2026 21:05. Exactly three routes registered in create_app:
POST /api/v1/construction/crm/activities (201)
POST /api/v1/construction/crm/activities/{id}/complete (200)
POST /api/v1/construction/crm/activities/{id}/cancel (200).
No additional PATCH/DELETE/list route is introduced by this increment.

Create body uses existing model names: type, due_at, timezone; optional lead_id,
client_id, project_id, responsible_user_id, comment. At least one parent is
required. due_at is an aware ISO date-time string; timezone is an IANA identifier.
The existing domain normalizes type/schedule/comment without altering supplied
values before the workflow hashes the command. The HTTP adapter maps type and
timezone to the existing activity_type/timezone_code workflow arguments.

Complete/cancel body accepts only required expected_row_version (positive BIGINT)
and optional nullable text result. exclude_unset preserves omitted result versus
explicit null. Omitted result is preserved by the existing SQL/service/workflow;
explicit null is passed as null, supplied text follows the existing normalization.
Different command with the same key yields 409 IDEMPOTENCY_KEY_REUSED.

Idempotency-Key required on all three, one nonblank header, max 255 characters.
Workflows use existing Platform encrypted response idempotency with shared scoped
session, trusted actor tenant/user, current membership/grants, resource scopes,
parent checks and caller transaction. Suite construction and module construction.core
must be enabled. Existing projects.create/edit permissions are retained; no new
permission code, assumed role exception, implicit grant or broad admin.logs.read.

Real workflows retain all reserve/business/audit/complete/commit behavior and
failure rollback. This HTTP layer does not implement a second transaction or
second audit. Replay retains the original status/version/body after fresh access
checks. Lifecycle failure is 409 conflict; stale version is 409 ROW_VERSION_CONFLICT;
missing/foreign resource 404; access 403; invalid input 422; internal faults 500.
Error envelopes use sanitized messages. Public typed projections validate expected
tenant, identifier/status/version/result context and exclude internal fields.
Terminal creation identity may differ from the completing actor; it is preserved.

Local tests send real ASGI HTTP requests through create_app using actual domain
services/workflows and controlled repositories plus encrypted in-memory records.
They cover replay, changed raw command, omitted/null/text result, current auth loss,
suite/module checks, key validation, typed input/output, version/lifecycle errors,
audit/commit failures and approved OpenAPI registration. Existing repository/service/
workflow/result tests remain in the selected regression. No live database/storage/
email operation runs in this installer.

Physical construction_activities storage and its native RLS integration remain
uncreated/unverified. Registering these endpoints does not by itself make the live
activity workflow available or complete CON-C011/Stage 1A acceptance. CON-C013 audit
correlation preparation is retained, but mutator integration/history remain next.
Next: CON_C011_HTTP_REGRESSION_AND_STORAGE_REVIEW.
