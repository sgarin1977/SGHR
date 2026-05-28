import logging
import os
from logging.handlers import RotatingFileHandler


DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(log_level: str | None = None) -> logging.Logger:
    level_name = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    logs_dir = os.getenv("LOG_DIR", "logs")
    log_file = os.getenv("LOG_FILE", os.path.join(logs_dir, "bot.log"))

    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)

    formatter = logging.Formatter(DEFAULT_LOG_FORMAT)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in list(root_logger.handlers):
        if getattr(handler, "_sghr_configured", False):
            root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler._sghr_configured = True
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler._sghr_configured = True
    root_logger.addHandler(file_handler)

    logging.captureWarnings(True)

    return root_logger


def setup_logger(name: str = "bot") -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


log = setup_logger("BOT")


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