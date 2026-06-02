# SGHR Server Environment Checklist

Use this checklist before starting SGHR Bot on a production or beta server.

## Required server access

- SSH access to server
- sudo access
- GitHub repository access
- Supabase/PostgreSQL access
- Telegram bot token
- Telegram admin user ids

## Required files

- /opt/sghr/.env exists
- /opt/sghr/.env owner is sghr:sghr
- /opt/sghr/.env permissions are 600
- /opt/sghr/venv exists
- /etc/systemd/system/sghr-bot.service exists

## Required .env values

Telegram:

- BOT_TOKEN
- ADMIN_TELEGRAM_IDS or SUPER_ADMIN_TELEGRAM_IDS

Database:

- DATABASE_URL
- SUPABASE_URL if used
- SUPABASE_SERVICE_ROLE_KEY if used
- SUPABASE_ANON_KEY if used

Tenant/bootstrap:

- DEFAULT_TENANT_SLUG
- DEFAULT_TENANT_NAME
- DEFAULT_ADMIN_TELEGRAM_IDS if used

Geo:

- GEO_PROVIDER
- NOMINATIM_USER_AGENT or provider-specific key
- GEO_DEFAULT_COUNTRY if used

Translation:

- TRANSLATION_PROVIDER
- TRANSLATION_API_KEY if provider requires it
- DEFAULT_LANGUAGE
- SUPPORTED_LANGUAGES

Logging:

- LOG_LEVEL
- LOG_DIR
- FATAL_ADMIN_ALERTS_ENABLED if used

Billing/admin:

- BILLING_ENABLED if used
- FINANCE_ADMIN_TELEGRAM_IDS if used
- MODERATOR_TELEGRAM_IDS if used

Security/rate limits:

- RATE_LIMIT_ENABLED
- CONTACT_RATE_LIMIT_WINDOW
- CONTACT_RATE_LIMIT_MAX
- PROFILE_EDIT_RATE_LIMIT_WINDOW
- PROFILE_EDIT_RATE_LIMIT_MAX

## Pre-start checks

Run from /opt/sghr:

PYTHONPATH=. ./venv/bin/python -m py_compile bot.py config.py database/models.py handlers/search.py handlers/billing.py handlers/settings.py fsm/specialist_form.py services/specialist.py services/privacy.py

PYTHONPATH=. ./venv/bin/python scripts/smoke_test_db.py
PYTHONPATH=. ./venv/bin/python scripts/smoke_test_geo.py
PYTHONPATH=. ./venv/bin/python scripts/smoke_test_translation.py

## Systemd checks

sudo systemctl daemon-reload
sudo systemctl enable sghr-bot
sudo systemctl start sghr-bot
sudo systemctl status sghr-bot --no-pager
sudo journalctl -u sghr-bot -n 100 --no-pager

## Expected logs

The bot should show:

bot_starting
bot_routers_registered
bot_polling_start
Start polling

## Manual smoke scenario

Check in Telegram:

- /start opens main menu
- settings opens
- interface language changes
- specialist registration works
- legal consent screen shows production documents, not test documents
- city/country selection works
- multi-specialty selection works
- work format selection works
- profile goes to moderation
- admin can approve or reject specialist
- search shows approved specialists
- filters work
- favorites add/remove/list works
- contact request can be created
- privacy screen opens
- hide profile works
- delete geo works
- data export request is created
