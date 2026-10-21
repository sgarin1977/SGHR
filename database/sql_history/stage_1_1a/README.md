# Stage 1 / 1A: archived applied SQL

This folder preserves eleven previously supplied, user-confirmed SQL artifacts.
The SQL is copied byte for byte. The manifest records its purpose and SHA-256.
Only corrected variants are included; failed variants and fixture scripts are excluded.

This is a historical subset, NOT a complete bootstrap or an ordered migration chain.
The missing earlier schema changes are listed in manifest.json. No Alembic revision
is marked applied by adding this folder. It makes no current database-readiness claim.

Do not execute the folder in filename order or attach it to application startup.
These guarded scripts include exact schema/ACL baselines from their original runs.
An old script can legitimately reject a database changed by a later approved script.
They have not been converted into universally replayable migrations, and no baseline
guard has been relaxed to make them pass. Any future deployment needs an explicit
upgrade plan for that database state. Nothing here creates Measurement entities.

The archive installer performs local file operations only. It never connects to
PostgreSQL or calls Supabase, Storage, Redis, Telegram or an email provider.
It preserves differing existing files and can be safely rerun on matching files.
