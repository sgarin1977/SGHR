# SGHR Backup and Restore Runbook

## Purpose

This document describes how to create, verify, and restore SGHR Beta database backups.

Backups are required before:

- production deploy
- Alembic migration
- bulk data script
- legal document cleanup
- specialist data cleanup
- billing or moderation data changes

## Backup policy from TZ

- Daily DB backup for staging and production
- Daily backup healthcheck
- Monthly restore test
- RPO Beta: 24h
- RTO Beta: 24h
- Manual backup before each production migration

## Critical data

The backup must include at least:

- users
- user_accounts
- user_roles
- tenants
- specialists
- specialist_professions
- specialist_locations
- specialist_categories
- professions
- legal_documents
- user_consents
- saved_specialists
- contact_requests
- contact_threads
- contact_messages
- complaints
- moderation/audit/event logs
- billing/payment/promotion tables
- data_subject_requests

## Create manual backup

Run this on a trusted machine with PostgreSQL client tools installed.

Set production database URL:

export DATABASE_URL='postgresql://USER:PASSWORD@HOST:PORT/DBNAME'

Create local backup directory:

mkdir -p backups

Create compressed PostgreSQL dump:

pg_dump "$DATABASE_URL" --format=custom --file="backups/sghr_$(date +%Y%m%d_%H%M%S).dump"

Check backup file:

ls -lh backups

## Verify backup

List dump contents:

pg_restore --list backups/<backup_file>.dump | head -n 80

Expected tables include:

public.users
public.user_accounts
public.specialists
public.specialist_professions
public.legal_documents
public.user_consents
public.contact_requests
public.saved_specialists

If the dump is empty, corrupted, or missing critical tables, do not deploy.

## Restore test database

Never test restore directly on production.

Set test database URL:

export TEST_DATABASE_URL='postgresql://USER:PASSWORD@HOST:PORT/TEST_DBNAME'

Restore into test database:

pg_restore --clean --if-exists --no-owner --dbname="$TEST_DATABASE_URL" backups/<backup_file>.dump

Run smoke tests against restored database:

PYTHONPATH=. python scripts/smoke_test_db.py
PYTHONPATH=. python scripts/smoke_test_geo.py
PYTHONPATH=. python scripts/smoke_test_translation.py

Manual test after restore:

- /start opens main menu
- specialist search works
- favorites list opens
- contact request can be created
- admin moderation panel opens

## Restore production during incident

Use only after approval.

Stop bot:

sudo systemctl stop sghr-bot

Create emergency backup of current production state:

pg_dump "$DATABASE_URL" --format=custom --file="backups/sghr_before_restore_$(date +%Y%m%d_%H%M%S).dump"

Restore approved backup:

pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" backups/<backup_file>.dump

Start bot:

sudo systemctl start sghr-bot

Check service:

sudo systemctl status sghr-bot --no-pager
sudo journalctl -u sghr-bot -n 100 --no-pager

## Supabase backup notes

If Supabase managed backups are used, verify:

- scheduled backups are enabled
- latest backup timestamp is visible
- restore procedure is known
- manual backup is created before migration
- service role key is not committed to git
- service role key is rotated after any suspected exposure

## Deploy backup checklist

Before production deploy:

- backup file exists
- backup file size is reasonable
- pg_restore --list works
- critical tables are present
- rollback commit is known
- bot can be stopped and started by systemd

After production deploy:

- bot logs are clean
- smoke tests pass
- manual Telegram smoke passes
