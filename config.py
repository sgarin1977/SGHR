import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")


def positive_int_env(
    name: str,
    default: int,
) -> int:
    raw_value = os.getenv(name, str(default))

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default

    return value if value > 0 else default


ROOT_SECURITY_ENCRYPTION_KEY = os.getenv(
    "ROOT_SECURITY_ENCRYPTION_KEY"
)
ROOT_TOTP_ISSUER = os.getenv(
    "ROOT_TOTP_ISSUER",
    "SGHR Root Recovery",
)
ROOT_PASSWORD_MIN_LENGTH = positive_int_env(
    "ROOT_PASSWORD_MIN_LENGTH",
    14,
)
ROOT_PASSWORD_MAX_ATTEMPTS = positive_int_env(
    "ROOT_PASSWORD_MAX_ATTEMPTS",
    5,
)
ROOT_MFA_MAX_ATTEMPTS = positive_int_env(
    "ROOT_MFA_MAX_ATTEMPTS",
    5,
)
ROOT_AUTH_COOLDOWN_SECONDS = positive_int_env(
    "ROOT_AUTH_COOLDOWN_SECONDS",
    900,
)
ROOT_SESSION_TTL_SECONDS = positive_int_env(
    "ROOT_SESSION_TTL_SECONDS",
    900,
)
ROOT_ACTION_TTL_SECONDS = positive_int_env(
    "ROOT_ACTION_TTL_SECONDS",
    300,
)
ROOT_RECOVERY_CODE_COUNT = positive_int_env(
    "ROOT_RECOVERY_CODE_COUNT",
    10,
)

ADMIN_TELEGRAM_IDS = [
    int(item.strip())
    for item in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if item.strip().isdigit()
]
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing in .env")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is missing in .env")
