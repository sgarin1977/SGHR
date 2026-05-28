import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")

ADMIN_TELEGRAM_IDS = [
    int(item.strip())
    for item in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if item.strip().isdigit()
]
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing in .env")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing in .env")
