# CON-C001 Backend Verification

Date: 2026-10-05
Status: The backend checks listed below are verified. Full Stage 1A and UI
acceptance are not claimed.

Previously verified:
- Targeted company-profile, identifier, bank-account and HTTP tests passed.
- Profile, identifier and bank-account writes recorded audit in the same
  transaction; rollback checks left no persisted test records.
- Live columns, constraints, indexes, grants and RLS policy definitions
  matched the confirmed CON-C001 contract.

Live checks completed with user-authorized temporary fixtures:
- The construction_api role enforced company-profile RLS with committed
  profiles in two tenants: each tenant saw its own profile and could not
  select the other tenant's profile.
- The existing private specialist-portfolio bucket stored a 70-byte PNG
  through the shared Files organization relation, entity_id = tenant_id.
- Upload completion verified the file, and company-profile attachment
  changed row_version from 1 to 2 with one matching audit event.
- Another active member of the same tenant obtained a signed URL and
  downloaded the exact PNG. A foreign tenant could not obtain a URL, and
  the original file was not available through the public object URL.
- Storage protocol corrections passed targeted regression:
  99 passed in 3.88s. Signing uses a JSON request; object inspection
  confirms NoSuchKey through structured metadata when HEAD returns 400.

Cleanup confirmed:
- Fixture run: 23ba591a-69b9-4642-9cb0-cc8fd1bf6fa0.
- The exact PNG was deleted through Storage API and its absence verified.
- All 18 temporary database rows were deleted in one transaction:
  2 tenants, 3 users, 3 memberships, 2 owner grants, 2 tenant languages,
  2 company profiles, 1 Files metadata row and 3 test audit events.
- All remaining fixture counts and test markers were zero.
  167 FK constraints were checked; no unexpected references remained.
  Deferred constraints were checked before commit.

Verification scope:
- Live checks used the currently configured Supabase project with explicit
  user authorization. No isolated staging environment is claimed.
- These were service-level integrations with real database and Storage.
  Full deployed HTTP login/authentication and UI flows remain unverified.
- The Construction company-and-presentation screen and final generated
  client-document workflow still require end-to-end verification.

Platform note:
- The existing enforce_tenant_default_language_active trigger function
  was repaired before fixture creation and passed 7 trigger checks.
  This repair remains in place after fixture cleanup.
- Platform-wide Alembic baseline/versioning remains tracked separately;
  it is not a CON-C001-specific schema defect.
