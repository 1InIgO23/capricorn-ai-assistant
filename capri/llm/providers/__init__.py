"""Provider modules. Every module in this package is imported automatically,
so adding a provider only requires adding one file with one class."""
from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger(__name__)

for _module in pkgutil.iter_modules(__path__):
    try:
        importlib.import_module(f"{__name__}.{_module.name}")
    except Exception:  # a broken provider must not take the app down
        log.exception("Could not load LLM provider module '%s'", _module.name)
