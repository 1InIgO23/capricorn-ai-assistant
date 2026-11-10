"""Logging to a rotating file in the app data folder, with secret redaction."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import log_dir

_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Register a string (an API key) that must never appear in the logs."""
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text: str) -> str:
    """Replace every registered secret found in *text* with a placeholder."""
    for secret in _SECRETS:
        if secret in text:
            text = text.replace(secret, "***REDACTED***")
    return text


class _RedactingFormatter(logging.Formatter):
    """Formatter that scrubs registered secrets from the final log line."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger (file + console). Safe to call more than once."""
    root = logging.getLogger()
    if getattr(root, "_capri_configured", False):
        return
    fmt = _RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        log_dir() / "capri.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    if sys.stderr is not None:  # pythonw.exe has no console streams
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        root.addHandler(console)
    root.setLevel(level)
    for noisy in ("urllib3", "httpx", "httpcore", "primp", "ddgs", "rquest"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._capri_configured = True  # type: ignore[attr-defined]
