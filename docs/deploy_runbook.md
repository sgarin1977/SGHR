# SGHR Beta Deploy Runbook

## Server prerequisites

Recommended OS: Ubuntu 24.04 LTS.

Commands:

sudo apt update
sudo apt install -y git python3.12 python3.12-venv python3-pip
sudo useradd --system --create-home --shell /bin/bash sghr
sudo mkdir -p /opt/sghr
sudo chown -R sghr:sghr /opt/sghr

## First deploy

Commands:

sudo -u sghr git clone https://github.com/sgarin1977/SGHR.git /opt/sghr
cd /opt/sghr
sudo -u sghr python3.12 -m venv venv
sudo -u sghr ./venv/bin/pip install --upgrade pip
sudo -u sghr ./venv/bin/pip install -r requirements.txt
sudo -u sghr cp .env.example .env
sudo chmod 600 /opt/sghr/.env
sudo chown sghr:sghr /opt/sghr/.env

Fill /opt/sghr/.env with production values from docs/server_env_checklist.md.

## Database checks

Commands:

cd /opt/sghr
sudo -u sghr PYTHONPATH=. ./venv/bin/python scripts/check_alembic_version.py
sudo -u sghr PYTHONPATH=. ./venv/bin/alembic current
sudo -u sghr PYTHONPATH=. ./venv/bin/alembic upgrade head

## Smoke checks

Commands:

cd /opt/sghr
sudo -u sghr PYTHONPATH=. ./venv/bin/python -m py_compile bot.py config.py database/models.py handlers/search.py handlers/billing.py handlers/settings.py fsm/specialist_form.py services/specialist.py services/privacy.py services/translation.py scripts/process_translation_jobs.py
sudo -u sghr PYTHONPATH=. ./venv/bin/python scripts/smoke_test_db.py
sudo -u sghr PYTHONPATH=. ./venv/bin/python scripts/smoke_test_geo.py
sudo -u sghr PYTHONPATH=. ./venv/bin/python scripts/smoke_test_translation.py

## Install systemd service

Commands:

sudo cp /opt/sghr/deploy/systemd/sghr-bot.service /etc/systemd/system/sghr-bot.service
sudo systemctl daemon-reload
sudo systemctl enable sghr-bot
sudo systemctl start sghr-bot

## Verify service

Commands:

sudo systemctl status sghr-bot --no-pager
sudo journalctl -u sghr-bot -n 100 --no-pager

Expected logs:

bot_starting
bot_routers_registered
bot_polling_start
Start polling

## Update deploy

Commands:

cd /opt/sghr
sudo -u sghr git checkout main
sudo -u sghr git pull origin main
sudo -u sghr ./venv/bin/pip install -r requirements.txt
sudo -u sghr PYTHONPATH=. ./venv/bin/alembic upgrade head
sudo systemctl restart sghr-bot
sudo journalctl -u sghr-bot -n 100 --no-pager

## Rollback

Commands:

cd /opt/sghr
sudo -u sghr git log --oneline -10
sudo -u sghr git checkout <commit_hash>
sudo systemctl restart sghr-bot
sudo journalctl -u sghr-bot -n 100 --no-pager

Return to main after rollback:

cd /opt/sghr
sudo -u sghr git checkout main
sudo -u sghr git pull origin main
sudo systemctl restart sghr-bot

## Portfolio Storage Cleanup

Portfolio files use the private Supabase Storage bucket:

- Bucket: specialist-portfolio
- Deleted items are physically removed after 30 days
- Rejected items are physically removed after 90 days

### Local manual check

Run from the project directory:

PYTHONPATH=. python scripts/cleanup_portfolio_storage.py

Expected output:

portfolio_storage_cleanup_completed cleaned_count=0

### Install production cleanup timer

Commands:

sudo cp /opt/sghr/deploy/systemd/sghr-portfolio-cleanup.service /etc/systemd/system/
sudo cp /opt/sghr/deploy/systemd/sghr-portfolio-cleanup.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now sghr-portfolio-cleanup.timer

### Verify cleanup service

Commands:

sudo systemctl start sghr-portfolio-cleanup.service
sudo systemctl status sghr-portfolio-cleanup.service --no-pager
sudo systemctl list-timers sghr-portfolio-cleanup.timer --no-pager
sudo journalctl -u sghr-portfolio-cleanup.service -n 50 --no-pager

Expected log:

portfolio_storage_cleanup_completed cleaned_count=0


## Translation worker and LibreTranslate

Beta uses LibreTranslate as the translation provider behind TranslationService.

The bot itself does not run LibreTranslate. LibreTranslate must run as a separate local service/container on the VPS. The bot connects to it through TRANSLATION_BASE_URL.

## Start LibreTranslate on VPS

Commands:

sudo docker run -d \
  --name libretranslate \
  -p 127.0.0.1:5000:5000 \
  --restart unless-stopped \
  libretranslate/libretranslate:latest \
  --load-only en,ru,pt

## Check LibreTranslate health

Commands:

curl -sS http://127.0.0.1:5000/languages

Expected result:

JSON list with supported languages.

## Required .env values

Add to /opt/sghr/.env:

TRANSLATION_PROVIDER=libretranslate
TRANSLATION_BASE_URL=http://127.0.0.1:5000
TRANSLATION_API_KEY=
TRANSLATION_TIMEOUT_SECONDS=15
TRANSLATION_MAX_RETRIES=3
TRANSLATION_CACHE_ENABLED=true
TRANSLATION_WORKER_LIMIT=20

## Run translation retry worker manually

Commands:

cd /opt/sghr
sudo -u sghr PYTHONPATH=. ./venv/bin/python scripts/process_translation_jobs.py --limit 20

Expected output when there are no pending jobs:

translation_jobs_processed count=0 statuses={} limit=20

Expected output when jobs were translated:

translation_jobs_processed count=3 statuses={'translated': 3} limit=20

## Install translation retry worker timer

Commands:

sudo cp /opt/sghr/deploy/systemd/sghr-translation-worker.service /etc/systemd/system/
sudo cp /opt/sghr/deploy/systemd/sghr-translation-worker.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sghr-translation-worker.timer

## Verify translation worker timer

Commands:

sudo systemctl list-timers sghr-translation-worker.timer --no-pager
sudo systemctl start sghr-translation-worker.service
sudo systemctl status sghr-translation-worker.service --no-pager
sudo journalctl -u sghr-translation-worker.service -n 50 --no-pager

Expected log:

translation_jobs_processed count=...

## Required behavior when provider is unavailable

If LibreTranslate is unavailable:

- bot still delivers original message
- translation job is stored as retry
- after max retries job becomes dead_letter
- translation failure is logged in translation_logs