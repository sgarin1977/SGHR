# Approved shared Construction permission matrix

Authority: rev.12 Stage 1 defaults retained by the final customer decision dated
08.10.2026 in new clarification.txt. The earlier reduced matrix proposal is
superseded by that decision.

The common registry contains 48 permission codes. Default role counts are:
OWNER 48; ADMIN 47; SURVEYOR 12; ESTIMATOR 21; PROJECT_MANAGER 40;
EXECUTOR 10; OBSERVER 14.

Only construction.settings.manage is added to the previously implemented
registry. OWNER and ADMIN inherit it from the common role policy.
construction.contracts.activate remains OWNER-only. All five other role sets
are preserved exactly, including the retained read permissions and project
manager approvals explicitly confirmed by the customer.

The separate CONSTRUCTION_SETTINGS_DEFAULT_PERMISSIONS mapping is removed.
The settings service guard validates active canonical identity and delegates
to require_construction_access with construction.settings.manage and no
project/document target. Therefore only an applicable grant for the current
tenant authorizes organization settings writes. Role-only, forged, foreign,
project-only and document-only contexts do not authorize them.

POST/PATCH settings/company now use require_construction_permission as their
HTTP permission gate. Its denied permission response is 403 permission_required;
service scope denial retains 403 forbidden. GET membership-based reading is
preserved. Version checks, logo guards, transactions and audits are unchanged.

The resolver and role-options endpoint expose the shared policy. Default deny,
scope validation and delegation ceiling remain in force. No permission storage
table, new membership table or physical Supabase migration is introduced.

docs/construction_permission_matrix.json contains the complete sorted registry
and each default role set, counts, scope types and tenant-scope role restrictions.
Local regression does not establish Supabase RLS/concurrency acceptance.
