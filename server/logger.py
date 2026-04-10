"""
server/logger.py
────────────────
Syslog-style rotating file logger shared across all modules.
"""

import logging
import logging.handlers
import os


def setup_logging(log_dir: str = "logs") -> logging.Logger:
    """
    Configures and returns the shared 'StockServer' logger.

    Handlers:
        - StreamHandler  → stdout (INFO+)
        - RotatingFileHandler → logs/stockserver.log (DEBUG+, max 5 MB × 5 files)
    """
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("StockServer")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)

    rfh = logging.handlers.RotatingFileHandler(
        f"{log_dir}/stockserver.log", maxBytes=5 * 1024 * 1024, backupCount=5
    )
    rfh.setFormatter(fmt)
    rfh.setLevel(logging.DEBUG)

    logger.addHandler(ch)
    logger.addHandler(rfh)
    return logger


# Module-level singleton — import `log` from here everywhere
log = setup_logging()
