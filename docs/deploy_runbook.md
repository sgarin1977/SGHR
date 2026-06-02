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
sudo -u sghr PYTHONPATH=. ./venv/bin/python -m py_compile bot.py config.py database/models.py handlers/search.py handlers/billing.py handlers/settings.py fsm/specialist_form.py services/specialist.py services/privacy.py
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
