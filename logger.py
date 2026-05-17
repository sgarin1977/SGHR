import logging
from logging.handlers import RotatingFileHandler
import os

LOGS_DIR = "logs"
LOG_FILE = os.path.join(LOGS_DIR, "bot.log")
os.makedirs(LOGS_DIR, exist_ok=True)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
formatter = logging.Formatter(LOG_FORMAT)

def setup_logger(name="bot"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        # Файл
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding='utf-8')
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

        # Консоль
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        logger.addHandler(console_handler)

    return logger

# Глобальный логгер по умолчанию
log = setup_logger("BOT")

# Упрощённые вызовы
def log_eventr(msg: str):
    log.info(msg)

def log_debug(msg: str):
    log.debug(msg)

def log_warning(msg: str):
    log.warning(msg)

def log_error(msg: str):
    log.error(msg)

def log_exception(msg: str):
    log.exception(msg)

