from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .paths import APP_LOG


def setup_logging() -> None:
    APP_LOG.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    handler = RotatingFileHandler(APP_LOG, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
    root.addHandler(handler)
