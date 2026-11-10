"""Filesystem locations used by Capri.

Everything user specific (settings, API keys, logs, conversations) lives in
the per-user application data folder, never inside the project folder.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Capri"


def app_data_dir() -> Path:
    """Return (and create) the per-user data folder, e.g. ``%APPDATA%\\Capri``.

    The ``CAPRI_HOME`` environment variable overrides the location, which is
    handy for tests and for running a second, isolated profile.
    """
    override = os.environ.get("CAPRI_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_file() -> Path:
    """Path of the JSON settings file (also holds the API keys)."""
    return app_data_dir() / "settings.json"


def state_file() -> Path:
    """Path of the small runtime state file (mood, last remark...)."""
    return app_data_dir() / "state.json"


def log_dir() -> Path:
    """Folder that receives the rotating log files."""
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def conversations_dir() -> Path:
    """Folder that stores one JSON file per conversation."""
    path = app_data_dir() / "conversations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_root() -> Path:
    """Root of the project checkout (the folder that contains ``main.py``)."""
    return Path(__file__).resolve().parents[2]


def assets_dir() -> Path:
    """The ``assets`` folder shipped with the project."""
    return project_root() / "assets"


def character_assets_dir() -> Path:
    """Folder scanned for character images (``assets/character``)."""
    return assets_dir() / "character"


def theme_file() -> Path:
    """The editable theme file."""
    return assets_dir() / "theme.json"


def default_workspace() -> Path:
    """Default folder for scripts written by Capri."""
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "CapriWorkspace"
