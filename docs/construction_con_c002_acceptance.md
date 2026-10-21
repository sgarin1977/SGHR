# CON-C002 Backend Verification

Date: 2026-10-05
Status: The backend checks listed below are verified. Full Stage 1A and UI
acceptance are not claimed.

Contract:
- construction_tenant_settings is the optional tenant-specific presentation
  resource. Legal identifiers, bank accounts and signatory remain in the
  canonical Platform company profile and its child resources.
- NULL overrides inherit the shared profile. Blank text overrides normalize
  to NULL. Presentation fallback does not create legal company data.
- construction.settings.manage is a Stage 1A tenant permission with
  OWNER/ADMIN defaults; the Stage 1 matrix of 47 permissions is unchanged.
  Live write checks used real OWNER grants and canonical Platform actors.

Previously verified:
- Targeted settings, permission, presentation, logo, version and HTTP tests
  passed: 45 passed in 3.89s.
- Live settings columns, constraints, grants, RLS policies and SQL planning
  matched the confirmed contract.

Live checks with user-authorized temporary fixtures:
- construction_api was neither superuser nor BYPASSRLS and did not inherit
  table ownership. Each tenant read its own committed settings and profile;
  foreign records were hidden.
- Two settings records were created through the service and committed with
  matching construction_tenant_settings_created audit events.
- Active members could read settings; an ordinary member could not create
  or update them.
- Updates advanced row_version and recorded exact old/new changes. Stale
  versions were rejected without persisted changes or audit.
- A controlled failure after the real settings and audit flush rolled back
  both. Persisted state was checked through a separate Platform connection.
  The test used the returned flushed event because construction_api has
  INSERT permission on event_logs but no SELECT permission.
- Blank overrides restored inheritance. No-op updates preserved row_version,
  timestamps and audit. RLS rejected foreign INSERT and tenant reassignment
  in both directions.
- Changing the shared profile name and contacts propagated to fresh
  Construction reads for OWNER and another active member without rewriting
  module settings or creating duplicate module audit.
- Shared Files workflow uploaded two distinct private organization PNGs
  (70 and 107 bytes), entity_id = tenant_id, in specialist-portfolio.
  Another active member downloaded matching bytes; foreign tenant download
  and anonymous public object URLs were denied.
- Platform logo attachment changed profile A from version 2 to 3.
  Construction inherited the shared file_id, used a separate override at
  settings version 4, and returned to the shared logo after clearing the
  override at version 5. Audit matched all three changes.
- Unauthorized member updates and a foreign file reference were rejected.
  Clearing an already NULL override was a no-op. Both original files
  remained available after removing the override.

Cleanup confirmed:
- Fixture run: 1a38d8a3-eb62-4e44-967e-42ad7500a460.
- Both exact PNG objects were removed through Storage API after fingerprint
  and reference checks; their absence was confirmed.
- All 28 temporary public database rows were deleted in one transaction:
  2 tenants, 3 users, 3 memberships, 2 owner grants, 2 tenant languages,
  2 company profiles, 2 settings records, 2 Files rows and 10 audit events.
- All remaining fixture counts and test markers were zero.
  167 FK constraints and 468 UUID columns were checked; no unexpected
  references remained. Deferred constraints passed before commit.

Verification scope:
- The live checks used the currently configured Supabase project with user
  authorization. No isolated staging environment is claimed.
- These were service-level integrations with real database and Storage.
  Full deployed HTTP login/authentication, the company-and-presentation UI
  and the final generated client-document workflow remain unverified.
- No application source changes or schema changes were made during these
  checks. Platform baseline/versioning remains tracked separately.

Next ordered item:
- CON-C003: canonical legal identifiers, document-visible bank accounts,
  signatory and resolved company data for Construction documents.
