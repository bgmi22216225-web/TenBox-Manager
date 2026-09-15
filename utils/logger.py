"""
utils/logger.py
Central logging configuration. Import setup_logging() once from main.py.
"""

import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("V")
    root.setLevel(level)

    if root.handlers:
        # Avoid duplicate handlers on reload
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
